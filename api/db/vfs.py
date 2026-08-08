from typing_extensions import Self

import attrs
from api.db.models import *
from api.db.userinteractions import UserInteractions, InteractionResponseCodes
from api.db.auth import Auth
from api.db.filesystem import Filesystem
from api.db.cache import Cache
from api.misc.logger import Logger
from api.misc.responses import ResponseCodes as code
from typing import Optional, List, Dict
import pkgutil, importlib.util, datetime

from rest_framework.response import Response

VFS_API_VERSION = 1

@attrs.define(kw_only=True)
class VirtualRequest:
    request: Any
    actor: UserInteractions
    mount: Virtual
    subpath: List[str]
    config: dict
    method: str

class VirtualHandler:
    name: str = "" # unique
    version: str = "0.0.1"
    requires_api: int = VFS_API_VERSION
    service_user: Optional[UserInteractions] = None

    def on_mount(self, mount: Virtual, service_user: UserInteractions) -> None: ...
    def on_unmount(self, mount: Virtual) -> None: ...

    def get(self, ctx: VirtualRequest): raise NotImplementedError
    def head(self, ctx: VirtualRequest): raise NotImplementedError
    def put(self, ctx: VirtualRequest): raise NotImplementedError
    def patch(self, ctx: VirtualRequest): raise NotImplementedError
    def delete(self, ctx: VirtualRequest): raise NotImplementedError
    def list(self, ctx: VirtualRequest): raise NotImplementedError  # GET on the mount itself

class VFS:
    instance = None
    
    def __new__(cls) -> Self:
        if cls.instance == None:
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.handlers: Dict[str, VirtualHandler] = {}
        self.fs = Filesystem()
        self.auth = Auth()
        self.cache = Cache()
        self.logger = Logger("init.vfs", LoggerLevel.DATABASE)

    def register(self, plugin_spec):
        module = importlib.util.module_from_spec(plugin_spec)
        plugin_spec.loader.exec_module(module)
        plugin = getattr(module, "PLUGIN", None)

        if plugin is None: return self.logger.log(f"FAILED: Plugin at {plugin_spec.origin} is invalid")
        if not issubclass(plugin, VirtualHandler): return self.logger.log(f"FAILED: Plugin ({plugin_spec.origin}) main class is not a VirtualHandler subclass")

        if self.handlers.get(plugin.name, None) is not None: return self.logger.log(f"FAILED: Plugin ({plugin_spec.origin}) with same name is already registered")

        if self.fs.fs_collection.count_documents({"parent_id": None, "name": plugin.name, "node_type": INodeType.VIRTUAL}) == 0:
            query = UserInteractions(self.auth.root).signFSVirtual(Virtual(
                parent_id=None,
                name=plugin.name,
                node_type=INodeType.VIRTUAL,
                handler=plugin.name,
                created_at=datetime.datetime.now().timestamp()
            ))

            if query.status != InteractionResponseCodes.OK: 
                return self.logger.log(f"Failed to create mount for plugin ({plugin_spec.origin}) at '/{plugin.name}'")

        mount = self.fs.get_child_from_folder(None, plugin.name)
        if mount is None: return self.logger.log(f"Failed to create mount for plugin ({plugin_spec.origin}) at '/{plugin.name}'")

        mount = Virtual().from_dict(mount.to_dict())
        user = self.auth.get_service_user(plugin.name)
        if user is None: return self.logger.log(f"Failed to create service user for plugin ({plugin_spec.origin})") 
        service_user = UserInteractions(user)

        self.handlers[plugin.name] = plugin()
        self.handlers[plugin.name].on_mount(mount, service_user)
        self.logger.log(f"Registered Plugin: {plugin.name=}; {plugin.version=}; {plugin.requires_api=}. Mount='/{plugin.name}'")
        

    def unregister(self, plugin_name: str) -> bool:
        if self.handlers.get(plugin_name, None) is None: return False
        handler = self.handlers[plugin_name]
        del handler
        del self.handlers[plugin_name]
        self.logger.log(f"Unregistered Plugin: {plugin_name=}")
        return True

    def unmount(self, plugin_name: str) -> bool:
        mount = self.fs.get_child_from_folder(None, plugin_name)
        if mount is not None: mount = Virtual().from_dict(mount.to_dict())
        user = self.auth.get_service_user(plugin_name, True)
        handler = self.handlers.get(plugin_name, None)

        if handler is not None and mount is not None: handler.on_unmount(mount)
        if not self.unregister(plugin_name): return False

        if mount is not None:
            query = UserInteractions(self.auth.root).deleteDirectory(mount)
            if query.status != InteractionResponseCodes.OK: return False
        if user is not None and user._id is not None: 
            query = self.auth.delete_user(user._id)
            if not query: return False

        return True

    def discover(self, package: List[str] = ["plugins"]):
        plugins = pkgutil.iter_modules(package)
        for plugin in plugins:
            spec = plugin.module_finder.find_spec(plugin.name, None)
            self.register(spec)

    def dispatch(self, resolution: Resolution, request, user: User, method: str):
        if resolution.mount is None: return Response(status=code.UNAVAILABLE)
        if resolution.mount.handler is None: return Response(status=code.UNAVAILABLE)
        handler = self.handlers.get(resolution.mount.handler)
        if handler is None: return Response(status=code.UNAVAILABLE)

        virtual_request = VirtualRequest(
            request=request,
            actor=UserInteractions(user),
            mount=resolution.mount,
            config=resolution.mount.config,
            subpath=resolution.subpath,
            method=method
        )

        try:
            match method:
                case "get":
                    if resolution.subpath == []: return handler.list(virtual_request)
                    return handler.get(virtual_request)
                case "head":
                    return handler.head(virtual_request)
                case "put":
                    return handler.put(virtual_request)
                case "patch":
                    return handler.patch(virtual_request)
                case "delete":
                    return handler.delete(virtual_request)
        except NotImplementedError:
            return Response(status=code.NOT_IMPLEMENTED)
        return Response(status=code.MALFORMED)