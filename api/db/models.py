from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

import attrs
from bson import ObjectId, json_util
from typing_extensions import Self


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
class APIToken(Model):
    token: str          = ""
    created_at: float   = 0.0
    usage: APIUsage     = attrs.field(factory=APIUsage, converter=_as_usage)

@attrs.define(kw_only=True)
class RateLimitResponse(Model):
    allowed: bool
    tokens: float
    retry_after: float
    usage_policy: APIUsage = attrs.field(converter=_as_usage)