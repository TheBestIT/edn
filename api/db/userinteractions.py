from api.db.models import *
from api.misc.logger import Logger, LoggerLevel
from api.db.auth import Auth
from api.db.filesystem import Filesystem
from api.db.cache import Cache
from api.db.configs import *

from typing import List
from enum import Enum
import attrs

class InteractionResponseCodes(int, Enum):
    NONE = 0
    PERMISSION = 1
    OK = 2
    FAIL = 3

@attrs.define(kw_only=True)
class InteractionResponse:
    status: InteractionResponseCodes = InteractionResponseCodes.NONE
    message: str = ""

@attrs.define(kw_only=True)
class Cost:
    base: ConfigKeys | float = 1
    multiplier: float = 1

    def value(self):
        if isinstance(self.base, float) or isinstance(self.base, int): return self.base * self.multiplier
        base_value = Configs().get_key(self.base)
        if base_value is None: return 10
        return base_value * self.multiplier

class UserInteractions:
    def __init__(self, user: User) -> None:
        self.actor = user
        self.logger = Logger(f"database.auth.userActor(uid={self.actor._id})", LoggerLevel.DATABASE)
        self.auth_db_instance: Auth = Auth()
        self.fs_db_instance: Filesystem = Filesystem()

    def createGroup(self, name: str) -> Group | None:
        return self.auth_db_instance.generate_new_group(name, self.actor)

    def traverseFullPath(self, path: List[str]) -> List[Directory] | InteractionResponse:
        traversed = self.fs_db_instance.traverse_full_path(path)
        if traversed is None: return InteractionResponse()

        for dir in traversed:
            if dir.permissions.check_flag(PermissionFlags.READ, self.actor) == False:
                return InteractionResponse(
                    status=InteractionResponseCodes.PERMISSION,
                    message=f"Cannot open directory '{dir.name}': Permission denied"
                )

        return traversed

    def checkUnknownFlag(self, object, flag: PermissionFlags) -> bool:
        node_type = object.get("node_type", None)
        if node_type is None: return False

        match node_type:
            case INodeType.DIR:
                dir = Directory().from_dict(object)
                self.logger.log(f"checking for flag={flag.name} in dir ({dir._id}) -> d{dir.permissions.get_permission_string()}")
                return dir.permissions.check_flag(flag, self.actor)
            case INodeType.FILE:
                file = File().from_dict(object)
                self.logger.log(f"checking for flag={flag.name} in file ({file._id}) -> -{file.permissions.get_permission_string()}")
                return file.permissions.check_flag(flag, self.actor)
            case INodeType.SYMLINK:
                symlink = Symlink().from_dict(object)
                self.logger.log(f"checking for flag={flag.name} in symlink ({symlink._id}) -> l{symlink.permissions.get_permission_string()}")
                return symlink.permissions.check_flag(flag, self.actor)
            case INodeType.VIRTUAL:
                virtual = Virtual().from_dict(object)
                self.logger.log(f"checking for flag={flag.name} in virtual ({virtual._id}) -> c{virtual.permissions.get_permission_string()}")
                return virtual.permissions.check_flag(flag, self.actor)
        
        return False

    def changeNodePermissions(self, node: INode, permissions: Permissions) -> InteractionResponse:
        diff = node.permissions

        if self.actor.privileged():
            if permissions.owner is not None: diff.owner = permissions.owner
            if permissions.group is not None: diff.group = permissions.group
            if permissions.other is not None: diff.other = permissions.other
            self.fs_db_instance.fs_collection.update_one({"_id": node._id}, {"$set": {"permissions": attrs.asdict(diff, recurse=True)}})
            return InteractionResponse(status=InteractionResponseCodes.OK)

        if node.permissions.owner is None: return InteractionResponse(status=InteractionResponseCodes.FAIL)
        if self.actor._id == node.permissions.owner[0]: 
            query = node.permissions.check_flag(PermissionFlags.WRITE, user=self.actor)
            if query:
                if permissions.group is not None:
                    if permissions.group[0] not in self.actor.groups:
                        return InteractionResponse(status=InteractionResponseCodes.FAIL, message=f"Cannot change file group to a group you are not in")
                    diff.group = permissions.group
                if permissions.other is not None: diff.other = permissions.other
                self.fs_db_instance.fs_collection.update_one({"_id": node._id}, {"$set": {"permissions": attrs.asdict(diff, recurse=True)}})
                return InteractionResponse(status=InteractionResponseCodes.OK)

        return InteractionResponse(status=InteractionResponseCodes.PERMISSION, message="Permission Denied")

    def estimateTreeNodeDeleteCost(self, node: TreeNode) -> Cost:
        if node.iNode.node_type != INodeType.DIR: return Cost()
        cost = Cost(base=ConfigKeys.RATE_DELETEDIRCOST)
        if node.children is None or len(node.children) == 0: return Cost(base=ConfigKeys.RATE_DELETEDIRCOST) 
        for child in node.children:
            match child.iNode.node_type:
                case INodeType.DIR:
                    query: Cost = self.estimateTreeNodeDeleteCost(child)
                    cost = Cost(multiplier=query.value()+cost.value())
                case INodeType.FILE:
                    file = File().from_dict(child.iNode.to_dict())
                    if file.size is None: continue
                    file_cost = Cost(base=ConfigKeys.RATE_DELETEDBYTECOST, multiplier=file.size)
                    cost = Cost(multiplier=file_cost.value()+cost.value())
                case INodeType.SYMLINK:
                    symlink_cost = Cost(base=ConfigKeys.RATE_DELETEDSYMLINKCOST)
                    cost = Cost(multiplier=symlink_cost.value()+cost.value())
        return cost

    def deleteFile(self, file: File) -> InteractionResponse:
        if file.permissions.check_flag(PermissionFlags.WRITE, self.actor) == False:
            InteractionResponse(
                status=InteractionResponseCodes.PERMISSION,
                message=f"Cannot delete directory '{file.name}': Permission denied"
            )
        query = self.fs_db_instance.del_file(file)
        return InteractionResponse(status=InteractionResponseCodes.OK) if query else InteractionResponse(status=InteractionResponseCodes.FAIL)

    def deleteSymlink(self, symlink: Symlink) -> InteractionResponse:
        if symlink.permissions.check_flag(PermissionFlags.WRITE, self.actor) == False:
            InteractionResponse(
                status=InteractionResponseCodes.PERMISSION,
                message=f"Cannot delete directory '{symlink.name}': Permission denied"
            )
        query = self.fs_db_instance.del_symlink(symlink)
        return InteractionResponse(status=InteractionResponseCodes.OK) if query else InteractionResponse(status=InteractionResponseCodes.FAIL)
    
    def deleteDirectory(self, directory: Directory | Virtual) -> InteractionResponse:
        if directory._id == None: return InteractionResponse()

        dir_children = self.fs_db_instance.fs_collection.find({"parent_id": directory._id})

        for child in dir_children:
            match child["node_type"]:
                case INodeType.DIR:
                    self.deleteDirectory(Directory().from_dict(child))
                case INodeType.FILE:
                    self.deleteFile(File().from_dict(child))
                case INodeType.SYMLINK:
                    self.deleteSymlink(Symlink().from_dict(child))

        query = self.fs_db_instance.del_directory(directory)
        return InteractionResponse(status=InteractionResponseCodes.OK) if query else InteractionResponse(status=InteractionResponseCodes.FAIL)

    def deleteUnknown(self, node: dict) -> InteractionResponse:
        match node["node_type"]:
            case INodeType.DIR:
                dir = Directory().from_dict(node)
                return self.deleteDirectory(dir)
            case INodeType.FILE:
                file = File().from_dict(node)
                return self.deleteFile(file)
            case INodeType.SYMLINK:
                symlink = Symlink().from_dict(node)
                return self.deleteSymlink(symlink)

        return InteractionResponse()

    def signFSVirtual(self, virtual: Virtual) -> InteractionResponse:
        if self.actor._id is None: return InteractionResponse()
        permissions = Permissions(
            owner=(self.actor._id, PermissionFlags.READ | PermissionFlags.WRITE),
            group=(self.actor.groups[0], PermissionFlags.READ | PermissionFlags.WRITE),
            other=PermissionFlags.READ
        )

        virtual.permissions = permissions
        query = self.fs_db_instance.sign_new_virtual(virtual)
        return InteractionResponse(status=InteractionResponseCodes.OK, message="Success") if query else InteractionResponse(status=InteractionResponseCodes.FAIL)

    def predictUsage(self, cost: Cost) -> RateLimitPrediction:
        query = Cache().predict(self.actor, cost.value())
        self.logger.log(f"Query for operation cost prediction; est. cost={cost}. {query=}")
        return query