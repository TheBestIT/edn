from typing_extensions import Self

import datetime, uuid
from api.db.database import Database, Collection
from api.misc.logger import Logger, LoggerLevel
from typing import Optional
from api.db.models import User, Group

class Auth:
    instance = None

    def __new__(cls) -> Self:
        if cls.instance is None:
            cls.instance = super().__new__(cls)
        return cls.instance


    def __init__(self) -> None:
        self.logger = Logger("database.auth", LoggerLevel.DATABASE)
        self.users_collection = Database().get_collection("users")
        self.groups_collection = Database().get_collection("groups")
        self.logger.log("Got reference to 'users' and 'groups' Collections")
        self.users_collection.create_index("token", is_unique=True)

        # db sanity check
        if self.get_User_from_uid(0) is None:
            self.generate_new_user("root", force_id=0)

        root = self.get_User_from_uid(0)
        if root is None: self.logger.fail("Unable to get 'root' (uid=0) account")
        self.root: User = root
    
    def _get_next_available_id(self, target_collection: Collection) -> int:
        cursor = target_collection.find().sort({"_id": -1}).limit(1)
        id = None
        for result in cursor:
            result = User().from_dict(result)
            id = result._id

        if id is None: return 0
        if id >= 0 and id < 1000: id = 999
        return id+1

    def _get_next_available_uid(self) -> int:
        return self._get_next_available_id(self.users_collection)

    def _get_next_available_gid(self) -> int:
        return self._get_next_available_id(self.groups_collection)

    def generate_new_group(self, name: str, owner: Optional[User], force_id: Optional[int] = None) -> Group | None:
        if force_id is None: next_available_gid = self._get_next_available_gid()
        else:
            next_available_gid = force_id
            if self.get_Group_from_uid(next_available_gid) is not None:
                self.groups_collection.delete_one({"_id": next_available_gid})
        group = Group(
            _id=next_available_gid,
            name=name,
            created_at=datetime.datetime.now().timestamp(),
            owner=(owner._id if owner is not None else 0)
        )

        self.logger.log(group)
        query = self.groups_collection.insert_one(group)

        if query is None: return None

        if query.acknowledged:
            self.logger.log(f"Inserted new group ('{group.name}'; gid={group._id}) successfully")
            return group

        self.logger.log("Failed to insert new group")
        return None

    def delete_user(self, uid: int) -> bool:
        user = self.get_User_from_uid(uid)
        if user is None: return False
        user_group_gid = user.groups[0]
        if self.get_Group_from_uid(user_group_gid) is not None:
            query = self.groups_collection.delete_one({"_id": user_group_gid})
            if not query:
                self.logger.log(f"Unable to delete group with gid={user_group_gid} in deleting user with {uid=}")
                return False

        query = self.users_collection.delete_one({"_id": uid})
        if not query:
            self.logger.log(f"Unable to delete user with {uid=}")
            return False
        return True

    def generate_new_user(self, name: str, force_id: Optional[int] = None) -> User | None:
        if force_id is None: next_available_uid = self._get_next_available_uid()
        else:
            next_available_uid = force_id
            if self.get_User_from_uid(next_available_uid) is not None:
                query = self.delete_user(next_available_uid)
                if not query: return None

        root_instance = self.get_User_from_uid(0)
        user_group = self.generate_new_group(name, root_instance, force_id)

        if user_group is None: return None
        if user_group._id is None: return None

        user = User(
            _id=next_available_uid,
            token=str(uuid.uuid4()),
            name=name,
            groups=[user_group._id],
            created_at=datetime.datetime.now().timestamp()
        )

        self.logger.log(user.to_dict())
        query = self.users_collection.insert_one(user)

        if query is None:
            self.logger.log("uuid.uuid4() generated the same token two times...")
            self.groups_collection.delete_one({"_id": user_group._id})
            return self.generate_new_user(name) # recurse...

        if query.acknowledged:
            self.logger.log(f"Inserted new user ('{user.name}'; uid={user._id}) successfully")
            return user

        self.logger.log("Failed to insert new user")
        self.groups_collection.delete_one({"_id": user_group._id})
        return None

    def get_User_from_token_string(self, token: str) -> User | None:
        self.logger.log(f"Requested User object from token '{token}'")
        query = self.users_collection.find_one({"token": token})
        return User().from_dict(query) if query is not None else None

    def get_User_from_uid(self, uid: int) -> User | None:
        self.logger.log(f"Requested User object with {uid=}")
        query = self.users_collection.find_one({"_id": uid})
        return User().from_dict(query) if query is not None else None

    def get_Group_from_uid(self, gid: int) -> Group | None:
        self.logger.log(f"Requested Group object with {gid=}")
        query = self.groups_collection.find_one({"_id": gid})
        return Group().from_dict(query) if query is not None else None

    def get_User_from_META_headers(self, META) -> User | None:
        self.logger.log("Requested User from META Headers")
        authorization_header = META.get('HTTP_AUTHORIZATION')
        if authorization_header is None: return None
        token = authorization_header.replace("Bearer ", "")
        query = self.get_User_from_token_string(token)
        return query
