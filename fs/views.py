from typing import Optional
from rest_framework.views import APIView
from rest_framework.response import Response
from api.db.filesystem import Filesystem
from api.db.models import User, StorageNode, File, INodeType, Permissions, INode, Directory, PermissionFlags, Symlink, Resolution, _jsonable
from api.db.auth import Auth
from api.db.configs import ConfigKeys
from api.db.userinteractions import *
from api.db.vfs import VFS
from api.misc.responses import ResponseCodes as code
from api.misc.logger import Logger, LoggerLevel
import tempfile, hashlib, requests, re, datetime
from django.http import StreamingHttpResponse
import json

_UNSAFE = re.compile(r"[^\w.\- ]")

def sanitize_filename(name: str | None) -> str:
    if not name:
        return "/"
    name = name.replace("\\", "/").split("/")[-1]   # strip any path, incl. ../
    name = _UNSAFE.sub("", name).strip()            # drop control chars, separators
    return name[:255] or "blob"                     # cap length, never empty

def put_file(request, parent_id, filename, user: User):
    raw = request._request
    hasher = hashlib.sha256()
    fs = Filesystem()
    actor = UserInteractions(user)
    total_bytes = 0

    if user._id is None: return Response({"status": "Bad Request"}, code.MALFORMED)
    if user.groups is None: return Response({"status": "Bad Request"}, code.MALFORMED)

    permissions = Permissions(
        owner=(user._id, PermissionFlags.READ | PermissionFlags.WRITE),
        group=(user.groups[0], PermissionFlags.READ | PermissionFlags.WRITE)
    )

    with tempfile.SpooledTemporaryFile(max_size=fs.max_blob_size) as spool:
        while True:
            chunk = raw.read(8192)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > fs.max_blob_size:
                return Response({"status": "Content Too Large"}, code.TOO_LARGE)
            hasher.update(chunk)
            spool.write(chunk)
        
        blob_hash = hasher.hexdigest()
        spool.seek(0)

        content_type = request.content_type or "application/octet-stream"
        node_address = ""
        rate_query: Optional[RateLimitPrediction] = None

        existing_entry = fs.fs_collection.find_one({"hashed": blob_hash})
        if existing_entry is not None:
            existing_entry = File().from_dict(existing_entry)
            if existing_entry.parent_id == parent_id and existing_entry.name == filename: return Response({"status": "OK"}, code.SUCCESS)
            node_address = existing_entry.hosted_node_address
            rate_query = actor.predictUsage(Cost(base=ConfigKeys.RATE_EXISTINGCOST))
        else:
            # Ask fs for suitable node
            node: StorageNode | None = fs.get_first_suitable_node(total_bytes)
            if node is None:
                return Response({"status": "No Available Node found"}, code.INSUFFICIENT_STORAGE)

            # Check if Rate-Limit allows operation
            rate_query = actor.predictUsage(Cost(base=ConfigKeys.RATE_BYTECOST, multiplier=total_bytes))
            if not rate_query.allowed:
                return Response({"status": "Rate Limited"}, code.LIMITED, headers=rate_query.build_headers())

            Logger(f"upload:${blob_hash}", LoggerLevel.ENDPOINT).log(f"Calling PUT to node @{node.address}:{node.port} of file of {total_bytes=} bytes)")

            # Allow operation and passthrough blob
            node_request = requests.put(f"http://{node.address}:{node.port}/blob/{blob_hash}", data=spool)
            if node_request.status_code != 200 and node_request.status_code != 201:
                return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=rate_query.build_headers())

            node_address = node.address

        db_query = fs.sign_new_file(File(
            node_type=INodeType.FILE,
            parent_id=parent_id,
            hashed=blob_hash,
            content_type=content_type,
            name=filename,
            size=total_bytes,
            hosted_node_address=node_address,
            created_at=datetime.datetime.now().timestamp(),
            permissions=permissions
        ))

        if db_query == False:
            return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=rate_query.build_headers())

        return Response({"status": "OK"}, code.CREATED, headers=rate_query.apply().build_headers())


def put_dir(request, parent_id, name, user: User):
    fs = Filesystem()
    actor = UserInteractions(user)

    if user._id is None: return Response({"status": "Bad Request"}, code.MALFORMED)
    if user.groups is None: return Response({"status": "Bad Request"}, code.MALFORMED)

    permissions = Permissions(
        owner=(user._id, PermissionFlags.READ | PermissionFlags.WRITE),
        group=(user.groups[0], PermissionFlags.READ | PermissionFlags.WRITE)
    )

    rate_query = actor.predictUsage(Cost(base=ConfigKeys.RATE_DIRCOST))

    if not rate_query.allowed:
        return Response({"status": "Rate Limited"}, code.LIMITED, headers=rate_query.build_headers())

    query = fs.sign_new_dir(Directory(
        node_type=INodeType.DIR,
        parent_id=parent_id,
        name=name,
        permissions=permissions,
        created_at=datetime.datetime.now().timestamp()
    ))

    if query == False:
        if fs.fs_collection.exists({"parent_id": parent_id, "name": name}):
            return Response({"status": "Conflict"}, code.CONFLICT, headers=rate_query.build_headers())
        return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=rate_query.build_headers())

    return Response({"status": "OK"}, code.CREATED, headers=rate_query.apply().build_headers())

def put_symlink(request, parent_id, name, user: User):
    fs = Filesystem()
    actor = UserInteractions(user)

    if user._id is None: return Response({"status": "Bad Request"}, code.MALFORMED)
    if user.groups is None: return Response({"status": "Bad Request"}, code.MALFORMED)

    permissions = Permissions(
        owner=(user._id, PermissionFlags.READ | PermissionFlags.WRITE),
        group=(user.groups[0], PermissionFlags.READ | PermissionFlags.WRITE)
    )

    try:
        target = json.loads(request.body).get("target", None)
    except json.JSONDecodeError:
        return Response({"status": "Bad Request"}, code.MALFORMED)

    if target is None: return Response({"status": "Bad Request"}, code.MALFORMED)

    rate_query = actor.predictUsage(Cost(base=ConfigKeys.RATE_SYMLINKCOST))

    if not rate_query.allowed:
        return Response({"status": "Rate Limited"}, code.LIMITED, headers=rate_query.build_headers())

    query = fs.sign_new_symlink(Symlink(
        node_type=INodeType.SYMLINK,
        parent_id=parent_id,
        name=name,
        permissions=permissions,
        created_at=datetime.datetime.now().timestamp(),
        target=target
    ))

    if query == False:
        if fs.fs_collection.exists({"parent_id": parent_id, "name": name}):
            return Response({"status": "Conflict"}, code.CONFLICT, headers=rate_query.build_headers())
        return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=rate_query.build_headers())
    
    return Response({"status": "OK"}, code.CREATED, headers=rate_query.apply().build_headers())

def get_resolution(user: User, request_path: str) -> Resolution | InteractionResponse:
    path = request_path.split("/") if request_path[0] == '/' else str(f"/{request_path}").split("/")
    path = [item for item in path if item != '']
    
    item_name       = sanitize_filename(path[-1] if path else "")
    item_path       = path[:-1]
    item_path       = [sanitize_filename(dir) for dir in item_path]
    item_path.insert(0, "/")

    traversed_list = UserInteractions(user).traverseFullPath(item_path)
    if isinstance(traversed_list, InteractionResponse):
        return traversed_list

    if len(traversed_list) == len(item_path):
        parent_dir: Directory = traversed_list[-1]
        item = parent_dir.get_child(item_name)
        mount = None
        node = item_name
        if item is not None and item.node_type == INodeType.VIRTUAL: mount = Virtual().from_dict(item.to_dict())
        elif item is not None: node = item
        return Resolution(
            parents=traversed_list,
            node=node,
            mount=mount
        )

    # there is a virtual node involved

    mount_name = item_path[len(traversed_list)]
    virtual_path = item_path[len(traversed_list)+1:]
    virtual_path.append(item_name)

    mount = None
    parent_dir: Directory = traversed_list[-1]
    item = parent_dir.get_child(mount_name)
    if item is not None and item.node_type == INodeType.VIRTUAL: mount = Virtual().from_dict(item.to_dict())

    return Resolution(
        parents=traversed_list,
        mount=mount,
        subpath=virtual_path
    )

class TraverseView(APIView):
    def put(self, request, subpath="/"): # PermissionFlags.WRITE
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        resolution = get_resolution(user, subpath)
        if isinstance(resolution, InteractionResponse):
            return Response({"status": resolution.message}, code.MALFORMED if resolution.status == InteractionResponseCodes.NONE else code.FORBIDDEN)

        if resolution.mount is not None:
            return VFS().dispatch(resolution, request, user, "put")

        if not isinstance(resolution.node, str): return Response({"status": 'Conflict'}, code.CONFLICT)
        
        edn_mode_header = request.headers.get("X-EDN-Mode", None)

        parent_object = resolution.parents[-1]
        flag_query = UserInteractions(user).checkUnknownFlag(parent_object.to_dict(), PermissionFlags.WRITE)
        if not flag_query: return Response({"status": f"Permission Denied: {parent_object.name}"}, code.FORBIDDEN)

        match edn_mode_header:
            case None:
                return put_file(request, parent_object._id, resolution.node, user)
            case "file":
                return put_file(request, parent_object._id, resolution.node, user)
            case "directory":
                return put_dir(request, parent_object._id, resolution.node, user)
            case "symlink":
                return put_symlink(request, parent_object._id, resolution.node, user)
            
        return Response({"status": "Bad X-EDN-Mode Header"}, code.MALFORMED)

    def get(self, request, subpath="/"): # PermissionFlags.READ
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        resolution = get_resolution(user, subpath)
        if isinstance(resolution, InteractionResponse):
            return Response({"status": resolution.message}, code.MALFORMED if resolution.status == InteractionResponseCodes.NONE else code.FORBIDDEN)

        if resolution.mount is not None:
            return VFS().dispatch(resolution, request, user, "get")
        
        fs = Filesystem()
        actor = UserInteractions(user)
        
        if not isinstance(resolution.node, INode): return Response({"status": "Not Found"}, code.NOT_FOUND)
        item = resolution.node.to_dict()

        flag_query = UserInteractions(user).checkUnknownFlag(item, PermissionFlags.READ)
        if not flag_query: return Response({"status": f"Permission Denied: {item['name']}"}, code.FORBIDDEN)

        rate_query = actor.predictUsage(Cost(base=ConfigKeys.RATE_GETQUERYCOST))
        if not rate_query.allowed:
            return Response({"status": "Rate Limited"}, code.LIMITED, headers=rate_query.build_headers())

        match item["node_type"]:
            case INodeType.DIR:
                directory = Directory().from_dict(item)
                children = [_jsonable(child) for child in fs.fs_collection.find({"parent_id": directory._id, "_id": { "$ne": None } }, {"hosted_node_address": 0})]
                
                return Response(children, code.SUCCESS, headers=rate_query.apply().build_headers())
            case INodeType.FILE:
                file = File().from_dict(item)
                if file.hosted_node_address is None: return Response({"status": "Resource Unavailable"}, code.UNAVAILABLE, headers=rate_query.build_headers()) 
                storage_node = fs.get_node_from_address(file.hosted_node_address)
                if storage_node is None: return Response({"status": "Resource Unavailable"}, code.UNAVAILABLE, headers=rate_query.build_headers())
                node_url = f"http://{storage_node.address}:{storage_node.port}/blob/{file.hashed}"
                params = {"ct": file.content_type, "fn": file.name}

                fwd_headers = {}
                if "HTTP_RANGE" in request.META:
                    fwd_headers["Range"] = request.META["HTTP_RANGE"]

                upstream = requests.get(
                    node_url, params=params, headers=fwd_headers,
                    stream=True, timeout=(5, None),
                )

                if upstream.status_code == 404:
                    upstream.close()
                    return Response({"status": "Not Found"}, code.NOT_FOUND, headers=rate_query.build_headers())

                def body():
                    try:
                        for chunk in upstream.iter_content(64 * 1024):
                            yield chunk
                    finally:
                        upstream.close()

                resp = StreamingHttpResponse(
                    body(),
                    status=upstream.status_code,                      # 200 or 206
                    content_type=upstream.headers.get("Content-Type", "application/octet-stream"),
                    headers=rate_query.apply().build_headers()
                )
                for h in ("Content-Length", "Content-Range", "ETag",
                        "Content-Disposition", "Accept-Ranges", "Cache-Control"):
                    if h in upstream.headers:
                        resp[h] = upstream.headers[h]
                return resp
            case INodeType.SYMLINK:
                symlink = Symlink().from_dict(item)
                if symlink.target is None: return Response({"status": "Bad Symlink Target"}, code.MALFORMED, headers=rate_query.build_headers())
                return self.get(request, subpath=symlink.target)

        return Response({"status": "Bad Request"}, code.MALFORMED, headers=rate_query.build_headers())

    def head(self, request, subpath="/"): # PermissionFlags.READ
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        resolution = get_resolution(user, subpath)
        if isinstance(resolution, InteractionResponse):
            return Response({"status": resolution.message}, code.MALFORMED if resolution.status == InteractionResponseCodes.NONE else code.FORBIDDEN)

        if resolution.mount is not None:
            return VFS().dispatch(resolution, request, user, "head")
        
        if not isinstance(resolution.node, INode): return Response({"status": "Not Found"}, code.NOT_FOUND)
        item = resolution.node.to_dict()

        flag_query = UserInteractions(user).checkUnknownFlag(item, PermissionFlags.READ)
        if not flag_query: return Response({"status": f"Permission Denied: {item['name']}"}, code.FORBIDDEN)

        actor = UserInteractions(user)
        rate_query = actor.predictUsage(Cost(base=ConfigKeys.RATE_HEADQUERYCOST))
        if not rate_query.allowed:
            return Response({"status": "Rate Limited"}, code.LIMITED, headers=rate_query.build_headers())

        headers = rate_query.apply().build_headers()
        match item["node_type"]:
            case INodeType.DIR:
                item = Directory().from_dict(item)
                headers["X-EDN-Mode"] = "directory"
            case INodeType.FILE:
                item = File().from_dict(item)
                headers["X-EDN-Mode"] = "file"
                headers["Content-Size"] = item.size
                headers["X-Content-Type"] = item.content_type
            case INodeType.SYMLINK:
                item = Symlink().from_dict(item)
                headers["X-EDN-Mode"] = "symlink"
                headers["X-EDN-SymlinkTarget"] = item.target

        return Response(status=code.SUCCESS, headers=headers)

    def delete(self, request, subpath="/"): # PermissionFlags.WRITE
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        resolution = get_resolution(user, subpath)
        if isinstance(resolution, InteractionResponse):
            return Response({"status": resolution.message}, code.MALFORMED if resolution.status == InteractionResponseCodes.NONE else code.FORBIDDEN)

        if resolution.mount is not None:
            return VFS().dispatch(resolution, request, user, "delete")
        
        if not isinstance(resolution.node, INode): return Response({"status": "Not Found"}, code.NOT_FOUND)
        item = resolution.node.to_dict()

        flag_query = UserInteractions(user).checkUnknownFlag(item, PermissionFlags.WRITE)
        if not flag_query: return Response({"status": f"Permission Denied: {item['name']}"}, code.FORBIDDEN)

        fs = Filesystem()

        user_interaction = UserInteractions(user)

        match item["node_type"]:
            case INodeType.DIR:
                item = Directory().from_dict(item)
                dir_tree = fs.index_dir(item)
                est_query_cost = user_interaction.estimateTreeNodeDeleteCost(dir_tree)
                rate_query = user_interaction.predictUsage(est_query_cost)
                if not rate_query.allowed:
                    return Response({"status": "Rate Limited"}, code.LIMITED, headers=rate_query.build_headers())
                delete_query = user_interaction.deleteDirectory(item)
                return Response({"status": "Success"}, code.SUCCESS, headers=rate_query.apply().build_headers()) if delete_query.status == InteractionResponseCodes.OK else Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=rate_query.build_headers())
            case INodeType.FILE:
                item = File().from_dict(item)
                if item.size is None: return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR)
                rate_query = user_interaction.predictUsage(Cost(base=ConfigKeys.RATE_DELETEDBYTECOST, multiplier=item.size))
                if not rate_query.allowed:
                    return Response({"status": "Rate Limited"}, code.LIMITED, headers=rate_query.build_headers())
                delete_query = user_interaction.deleteFile(item)
                return Response({"status": "Success"}, code.SUCCESS, headers=rate_query.apply().build_headers()) if delete_query.status == InteractionResponseCodes.OK else Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=rate_query.build_headers())
            case INodeType.SYMLINK:
                item = Symlink().from_dict(item)
                rate_query = user_interaction.predictUsage(Cost(base=ConfigKeys.RATE_DELETEDSYMLINKCOST))
                if not rate_query.allowed:
                    return Response({"status": "Rate Limited"}, code.LIMITED, headers=rate_query.build_headers())
                delete_query = user_interaction.deleteSymlink(item)
                return Response({"status": "Success"}, code.SUCCESS, headers=rate_query.apply().build_headers()) if delete_query.status == InteractionResponseCodes.OK else Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=rate_query.build_headers())

        return Response({"status": "Bad Request"}, code.MALFORMED)

    def patch(self, request, subpath="/"): # PermissionFlags.WRITE
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        resolution = get_resolution(user, subpath)
        if isinstance(resolution, InteractionResponse):
            return Response({"status": resolution.message}, code.MALFORMED if resolution.status == InteractionResponseCodes.NONE else code.FORBIDDEN)

        if resolution.mount is not None:
            return VFS().dispatch(resolution, request, user, "patch")
        
        fs = Filesystem()
        
        if not isinstance(resolution.node, INode): return Response({"status": "Not Found"}, code.NOT_FOUND)
        target_item = resolution.node
        flag_query = UserInteractions(user).checkUnknownFlag(target_item.to_dict(), PermissionFlags.READ)
        if not flag_query: return Response({"status": f"Can't open file '{target_item.name}': Permission Denied"}, code.FORBIDDEN)

        operation = request.headers.get("X-EDN-Patch", None)
        if operation is None: return Response({"status": "Missing X-EDN-Patch Header"}, code.MALFORMED)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return Response({"status": "Bad Request"}, code.MALFORMED)

        path = data.get("path", None)
        name = data.get("name", None)

        if path is not None:
            resolution = get_resolution(user, path)
            if isinstance(resolution, InteractionResponse):
                return Response({"status": resolution.message}, code.MALFORMED if resolution.status == InteractionResponseCodes.NONE else code.FORBIDDEN)
    
            if resolution.mount is not None:
                return VFS().dispatch(resolution, request, user, "patch")
            
            if not isinstance(resolution.node, INode): return Response({"status": "Not Found"}, code.NOT_FOUND)

            if resolution.node.node_type != INodeType.DIR: return Response({"status": "Target is not a directory"}, code.MALFORMED)

        readflag_query = UserInteractions(user).checkUnknownFlag(resolution.node.to_dict(), PermissionFlags.READ)
        writeflag_query = UserInteractions(user).checkUnknownFlag(resolution.node.to_dict(), PermissionFlags.WRITE)
        if not readflag_query or not writeflag_query: return Response({"status": f"Permission Denied: {resolution.node.name}"}, code.FORBIDDEN)

        query: Optional[bool] = None
        match operation:
            case "move":
                if path is None: return Response({"status": "Bad Request"}, code.MALFORMED)
                if target_item._id == resolution.node._id or any(p._id == target_item._id for p in resolution.parents): return Response({"status": "Conflict"}, code.CONFLICT)
                if target_item.parent_id == resolution.node._id: return Response({"status": "ok"}, code.SUCCESS)
                query = fs.fs_collection.update_one({"_id": target_item._id}, {"$set": {"parent_id": resolution.node._id}})
            case "rename":
                if name is None: return Response({"status": "Bad Request"}, code.MALFORMED)
                name = sanitize_filename(name)
                if not UserInteractions(user).checkUnknownFlag(target_item.to_dict(), PermissionFlags.WRITE): return Response({"status": f"Permission Denied: {target_item.name}"}, code.FORBIDDEN)
                update_query = fs.fs_collection.update_one({"_id": target_item._id}, {"$set": {"name": name}})
                if update_query is None: return Response({"status": "Conflict"}, code.CONFLICT)
                query = update_query
            case "copy":
                if path is None: return Response({"status": "Bad Request"}, code.MALFORMED)
                if target_item._id == resolution.node._id: return Response({"status": "Conflict"}, code.CONFLICT)
                if target_item.parent_id == resolution.node._id: return Response({"status": "Conflict"}, code.CONFLICT)
                copy_query = fs.copy_item(target_item.to_dict(), resolution.node._id)
                if copy_query is None: return Response({"status": "Conflict"}, code.CONFLICT)
                query = copy_query

        if query is None: return Response({"status": "Invalid X-EDN-Patch Header"}, code.MALFORMED)

        return Response({"status": "ok"}, code.SUCCESS) if query else Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR)