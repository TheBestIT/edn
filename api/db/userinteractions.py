from api.db.models import User, Group, PermissionFlags, INodeType, Directory, File, Symlink
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

    def traverseFullPath(self, path: List[str]) -> List[ObjectId] | InteractionResponse:
        traversed = self.fs_db_instance.traverse_full_path(path)
        if traversed is None: return InteractionResponse()

        reparsed = []
        for dir in traversed:
            if dir.permissions.check_flag(PermissionFlags.READ, self.actor) == False:
                return InteractionResponse(
                    status=InteractionResponseCodes.PERMISSION,
                    message=f"Cannot open directory '{dir.name}': Permission denied"
                )
            reparsed.append(dir._id)
        return reparsed

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
        
        return False
