from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, List, Generic, Tuple, Callable
from typing_extensions import TypeVar
from enum import Enum

import attrs, requests
from bson import ObjectId, json_util
from typing_extensions import Self
from api.misc.logger import Logger, LoggerLevel

def _jsonable(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value

IdT = TypeVar("IdT", default=ObjectId)

@attrs.define(kw_only=True)
class Model(Generic[IdT]):
    _id: Optional[IdT] = attrs.field(default=None, alias="_id")

    def to_dict(self) -> dict[str, Any]:
        data = attrs.asdict(self, recurse=True)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        kwargs = {
            field.alias: data[field.name]
            for field in attrs.fields(cls)
            if field.name in data
        }
        return cls(**kwargs)

    def to_json(self, **kwargs: Any) -> str:
        return json_util.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, raw: str | bytes) -> Self:
        return cls.from_dict(json_util.loads(raw))

    def to_public(self) -> dict[str, Any]:
        data = self.to_dict()
        oid = data.pop("_id", None)
        public: dict[str, Any] = {"id": (str(oid) if isinstance(oid, ObjectId) else oid)} if oid is not None else {}
        public.update({k: _jsonable(v) for k, v in data.items()})
        return public

# Rate-Limiting

@attrs.define(kw_only=True)
class APIUsage:
    bucket_size: int = 1000
    bucket_refill_rate: float = 5.0  # tokens per second

def _as_usage(value: Any) -> APIUsage:
    if isinstance(value, APIUsage):
        return value
    if value is None:
        return APIUsage()
    return APIUsage(**value)

@attrs.define(kw_only=True)
class RateLimitResponse:
    allowed: bool
    tokens: float
    retry_after: float
    usage_policy: APIUsage = attrs.field(converter=_as_usage)

    def build_headers(self) -> dict:
        headers = {
            "X-RateLimit-Limit": self.usage_policy.bucket_size,
            "X-RateLimit-Remaining": self.tokens,
            "X-RateLimit-Reset": (self.usage_policy.bucket_size - self.tokens) / self.usage_policy.bucket_refill_rate
        }
        if not self.allowed:
            headers["Retry-After"] = self.retry_after
        return headers

@attrs.define(kw_only=True)
class RateLimitPrediction(RateLimitResponse):
    apply: Callable[[], RateLimitResponse] = attrs.field(eq=False, repr=False)

# API/Auth

@attrs.define(kw_only=True)
class User(Model[int]): # _id is mapped to an int in this call
    token: str          = ""
    name: str           = ""
    created_at: float   = 0.0
    usage: APIUsage     = attrs.field(factory=APIUsage, converter=_as_usage)
    groups: List[int]   = []
    nologin: bool       = False

@attrs.define(kw_only=True)
class Group(Model[int]): # _id is mapped to an int in this call
    name: Optional[str] = None
    created_at: Optional[float] = None
    owner: Optional[int] = None

# StorageNode

class NodeStatus(int, Enum):
    UNAVAILABLE = -1
    DEAD = 0
    ALIVE = 1

@attrs.define(kw_only=True)
class NodeStorageHealth:
    root: str               = "NULL"
    writable: bool          = False
    total_bytes: int        = 0
    available_bytes: int    = 0
    used_percent: float     = 0.0

def _as_NodeStorageHealth(value: Any) -> NodeStorageHealth:
    if isinstance(value, NodeStorageHealth):
        return value
    if value is None:
        return NodeStorageHealth()
    return NodeStorageHealth(**value)

@attrs.define(kw_only=True)
class NodeHealth:
    status: NodeStatus          = NodeStatus.DEAD
    last_heartbeat: float       = 0
    version: str                = "NULL"
    uptime_seconds: int         = 0
    store: NodeStorageHealth    = attrs.field(factory=NodeStorageHealth, converter=_as_NodeStorageHealth)

def _as_NodeHealth(value: Any) -> NodeHealth:
    if isinstance(value, NodeHealth):
        return value
    if value is None:
        return NodeHealth()
    return NodeHealth(**value)

@attrs.define(kw_only=True)
class StorageNode(Model):
    address: str
    port: int = 8455

    health: NodeHealth = attrs.field(factory=NodeHealth, converter=_as_NodeHealth)

    def check_health(self):
        logger = Logger(f"StorageNode@{self.address}:{self.port}", LoggerLevel.VERBOSE)
        try:
            query = requests.request("GET", f"http://{self.address}:{self.port}/health")
        except:
            if self.health.status != NodeStatus.DEAD:
                self.health.status = NodeStatus.DEAD
                logger.log("Marking node as DEAD.")
            return

        if query.status_code != 200:
            if self.health.status != NodeStatus.DEAD:
                self.health.status = NodeStatus.DEAD
                logger.log("Marking node as DEAD.")
            return
        
        data = query.json()
        last_heath                  = self.health.status
        self.health.status          = NodeStatus.ALIVE if data["status"] == "ok" else NodeStatus.UNAVAILABLE
        self.health.last_heartbeat  = datetime.now().timestamp()
        self.health.version         = data["version"]
        self.health.uptime_seconds  = data["uptime_seconds"]

        if self.health.status == NodeStatus.ALIVE:
            self.health.store.root              = data["store"]["root"]
            self.health.store.writable          = data["store"]["writable"]
            self.health.store.total_bytes       = data["store"]["total_bytes"]
            self.health.store.available_bytes   = data["store"]["available_bytes"]
            self.health.store.used_percent      = data["store"]["used_percent"]

        if last_heath != self.health.status: logger.log(f"Marking node as {self.health.status.name}")

# Filesystem

class INodeType(int, Enum):
    DIR = 0
    FILE = 1
    SYMLINK = 2
    VIRTUAL = 3

class PermissionFlags(int, Enum):
    READ = 0x10
    WRITE = 0x20

@attrs.define(kw_only=True)
class Permissions:
    owner: Optional[Tuple[int, int]] = None 
    group: Optional[Tuple[int, int]] = None 
    other: Optional[int] = 0 

    def check_flag(self, flag: PermissionFlags, user: User) -> bool:
        if user._id == 0 or any(gid == 0 for gid in user.groups): return True
        if self.owner is None or self.group is None: return False

        if self.owner[0] == user._id: return self.owner[1] & flag == flag
        if any(gid == self.group[0] for gid in user.groups): return self.group[1] & flag == flag

        return False if self.other is None else self.other & flag == flag

    def get_permission_string(self) -> str:
        string = ""
        if self.owner is not None:
            string += 'r' if self.owner[1] & PermissionFlags.READ == PermissionFlags.READ else '-'
            string += 'w' if self.owner[1] & PermissionFlags.WRITE == PermissionFlags.WRITE else '-'
        else: string += '--'

        if self.group is not None:
            string += 'r' if self.group[1] & PermissionFlags.READ == PermissionFlags.READ else '-'
            string += 'w' if self.group[1] & PermissionFlags.WRITE == PermissionFlags.WRITE else '-'
        else: string += '--'

        if self.other is not None:
            string += 'r' if self.other & PermissionFlags.READ == PermissionFlags.READ else '-'
            string += 'w' if self.other & PermissionFlags.WRITE == PermissionFlags.WRITE else '-'
        else: string += '--'

        return string

def _as_Ownership(value: Any) -> Permissions:
    if isinstance(value, Permissions):
        return value
    if value is None:
        return Permissions()
    return Permissions(**value)


@attrs.define(kw_only=True)
class INode():
    _id: Optional[ObjectId] = None
    parent_id: Optional[ObjectId] = None
    name: Optional[str] = None 
    node_type: Optional[INodeType] = None

    permissions: Permissions = attrs.field(factory=Permissions, converter=_as_Ownership)

    _drop_if_none = ("_id", "parent_id")
    def to_dict(self) -> dict[str, Any]:
        data = attrs.asdict(self, recurse=True)
        for key in self._drop_if_none:
            if data.get(key) is None:
                data.pop(key, None)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        kwargs = {
            field.alias: data[field.name]
            for field in attrs.fields(cls)
            if field.name in data
        }
        return cls(**kwargs)

    def to_json(self, **kwargs: Any) -> str:
        return json_util.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, raw: str | bytes) -> Self:
        return cls.from_dict(json_util.loads(raw))

    def to_public(self) -> dict[str, Any]:
        data = self.to_dict()
        oid = data.pop("_id", None)
        public: dict[str, Any] = {"id": str(oid)} if oid is not None else {}
        public.update({k: _jsonable(v) for k, v in data.items()})
        return public

@attrs.define(kw_only=True)
class Virtual(INode):
    handler: Optional[str] = None
    config: dict = attrs.field(factory=dict) # plugin-controlled configs
    cost: int = 10
    created_at: Optional[float] = None

@attrs.define(kw_only=True)
class File(INode):
    hashed: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None
    created_at: Optional[float] = None 
    hosted_node_address: Optional[str] = None

@attrs.define(kw_only=True)
class Directory(INode):
    created_at: Optional[float] = None

    def get_child_dir(self, child_name: str) -> Directory | None:
        Logger(f"INode:DIR-{self._id}").log(f"Traversing to '{child_name}'")
        from api.db.filesystem import Filesystem
        return Filesystem().get_child_dir_from_folder(self._id, child_name)

    def get_child(self, child_name: str) -> INode | None:
        Logger(f"INode:DIR-{self._id}").log(f"Getting '{child_name}'")
        from api.db.filesystem import Filesystem
        return Filesystem().get_child_from_folder(self._id, child_name)


@attrs.define(kw_only=True)
class Symlink(INode):
    target: Optional[str] = None
    created_at: Optional[float] = None 

@attrs.define(kw_only=True)
class TreeNode:
    iNode: INode
    children: Optional[List[TreeNode]] = None

@attrs.define(kw_only=True)
class Resolution:
    parents: List[Directory]
    node: Optional[INode | str] = None
    mount: Optional[Virtual] = None
    subpath: List[str] = []