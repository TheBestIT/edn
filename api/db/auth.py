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

        if self.get_Group_from_uid(27) is None:
            self.generate_new_user("sudo", force_id=27, nologin=True)

        if self.get_Group_from_uid(100) is None:
            self.generate_new_user("users", force_id=100, nologin=True)

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

    def _get_next_available_sid(self, target_collection: Collection) -> int | None:
        cursor = target_collection.find({"_id": {"$lt": 1000}}).sort({"_id": -1}).limit(1)
        id = None
        for result in cursor:
            result = User().from_dict(result)
            id = result._id

        if id is None: return None
        if id > 999: return None
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

    def get_service_user(self, handler_name: str, ifexist: bool = False) -> User | None:
        query = self.users_collection.find_one({"name": f"plugin:{handler_name}"})
        if query is not None: return User().from_dict(query)
        if ifexist: return None
        next_available_sid = self._get_next_available_sid(self.users_collection)
        return self.generate_new_user(f"plugin:{handler_name}", next_available_sid, True)

    def generate_new_user(self, name: str, force_id: Optional[int] = None, nologin: bool = False) -> User | None:
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
            created_at=datetime.datetime.now().timestamp(),
            nologin=nologin
        )

        if not nologin: user.groups.append(100)

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
        if isinstance(query, User):
            if query.nologin: return None
        return query
