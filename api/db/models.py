from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional
from enum import Enum

import attrs, requests
from bson import ObjectId, json_util
from typing_extensions import Self
from api.misc.logger import Logger


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


@attrs.define(kw_only=True)
class Model:
    _id: Optional[ObjectId] = None

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
        public: dict[str, Any] = {"id": str(oid)} if oid is not None else {}
        public.update({k: _jsonable(v) for k, v in data.items()})
        return public

# Rate-Limiting

@attrs.define(kw_only=True)
class APIUsage:
    bucket_size: int = 1000
    bucket_refill_rate: float = 5.0  # tokens per second
    bucket_expire_rate: float = attrs.field(
        default=attrs.Factory(
            lambda self: self.bucket_size / self.bucket_refill_rate,
            takes_self=True,
        )
    )

def _as_usage(value: Any) -> APIUsage:
    if isinstance(value, APIUsage):
        return value
    if value is None:
        return APIUsage()
    return APIUsage(**value)

@attrs.define(kw_only=True)
class RateLimitResponse(Model):
    allowed: bool
    tokens: float
    retry_after: float
    usage_policy: APIUsage = attrs.field(converter=_as_usage)

# API/Auth

@attrs.define(kw_only=True)
class APIToken(Model):
    token: str          = ""
    created_at: float   = 0.0
    usage: APIUsage     = attrs.field(factory=APIUsage, converter=_as_usage)

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
        logger = Logger(f"StorageNode@{self.address}:{self.port}")
        try:
            query = requests.request("GET", f"http://{self.address}:{self.port}/health")
        except:
            self.health.status = NodeStatus.DEAD
            logger.log("Marking node as DEAD.")
            return

        if query.status_code != 200:
            self.health.status = NodeStatus.DEAD
            logger.log("Marking node as DEAD.")
            return
        
        data = query.json()
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

        logger.log(f"Marking node as {self.health.status.name}")

@attrs.define(kw_only=True)
class File(Model):
    hashed: str
    content_type: str
    filename: str
    size: int
    created_at: float
    hosted_node_address: str