from api.db.models import User, Group, PermissionFlags, INodeType, Directory, File, Symlink, Virtual, Permissions
from api.misc.logger import Logger, LoggerLevel
from api.db.auth import Auth
from api.db.filesystem import Filesystem

from bson import ObjectId
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