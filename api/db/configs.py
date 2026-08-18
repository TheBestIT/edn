from typing_extensions import Self

from api.db.database import Database
from api.misc.logger import Logger, LoggerLevel
from api.db.models import Model
import attrs
from enum import Enum

class ConfigKeys(Enum):
    RATE_BYTECOST       = 0
    RATE_DIRCOST        = 1
    RATE_SYMLINKCOST    = 2
    RATE_EXISTINGCOST   = 3
    RATE_GETQUERYCOST   = 4
    RATE_HEADQUERYCOST  = 5

    RATE_DELETEDIRCOST      = 6
    RATE_DELETEDBYTECOST    = 7
    RATE_DELETEDSYMLINKCOST = 8


@attrs.define(kw_only=True)
class RateUsage(Model[int]):
    per_byte_cost: float        = 0.0001192093 # * the bytes
    per_dir_cost: float         = 0.1
    per_symlink_cost: float     = 0.0001
    per_existing_cost: float    = 0.0001
    per_get_query_cost: float   = 0.001
    per_head_query_cost: float  = 0.0001

    per_deleted_dir_cost: float     = 0.001
    per_deleted_byte_cost: float    = 0.0000119 # * the bytes
    per_deleted_symlink_cost: float = 0.0001

RATE_USAGE_ID = 0

class Configs:
    instance = None

    def __new__(cls) -> Self:
        if cls.instance is None:
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return 
        _initialized = True

        self.logger = Logger("init.configs", LoggerLevel.DATABASE)
        self.collection = Database().get_collection("configs")

        if self.collection.find_one({"_id": RATE_USAGE_ID}) is None:
            query = self.collection.insert_one(RateUsage(_id = RATE_USAGE_ID))
            if query is None: self.logger.fail("Failed to create RateUsage Config")

        rate_usage = self.collection.find_one({"_id": RATE_USAGE_ID})
        if rate_usage is None: self.logger.fail("Failed to get RateUsage Config")
        self._rate_usage: RateUsage = RateUsage().from_dict(rate_usage)

    def get_key(self, key: ConfigKeys):
        match key:
            case ConfigKeys.RATE_BYTECOST:
                return self._rate_usage.per_byte_cost
            case ConfigKeys.RATE_DIRCOST:
                return self._rate_usage.per_dir_cost
            case ConfigKeys.RATE_SYMLINKCOST:
                return self._rate_usage.per_symlink_cost
            case ConfigKeys.RATE_EXISTINGCOST:
                return self._rate_usage.per_existing_cost
            case ConfigKeys.RATE_GETQUERYCOST:
                return self._rate_usage.per_get_query_cost
            case ConfigKeys.RATE_GETQUERYCOST:
                return self._rate_usage.per_head_query_cost
            case ConfigKeys.RATE_DELETEDIRCOST:
                return self._rate_usage.per_deleted_dir_cost
            case ConfigKeys.RATE_DELETEDBYTECOST:
                return self._rate_usage.per_deleted_byte_cost
            case ConfigKeys.RATE_DELETEDSYMLINKCOST:
                return self._rate_usage.per_deleted_symlink_cost
        return None