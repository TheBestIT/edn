from typing_extensions import Self

import pymongo
from pymongo.collection import Collection as MongoCollection
from pymongo.results import InsertOneResult, BulkWriteResult
from pymongo.errors import DuplicateKeyError
from pymongo.cursor import Cursor
from typing import Any, List, Optional
import json, os
from api.misc.logger import Logger, LoggerLevel
from api.db.models import Model, INode, INodeType, File, Directory, Symlink

class Collection:
    def __init__(self, collection: MongoCollection) -> None:
        self.logger = Logger(f"database.collection.{collection.name}", LoggerLevel.VERBOSE)
        self.collection = collection

    def insert_one(self, document: Model[Any] | INode) -> InsertOneResult | None:
        try:
            result = self.collection.insert_one(document.to_dict())
        except DuplicateKeyError:
            self.logger.log(f"insert_one called with duplicate {document=}")
            return None
        document._id = result.inserted_id
        self.logger.log(f"insert_one called on collection with data: {document.to_dict()}. {result.acknowledged=}; {result.inserted_id=}")
        return result

    def insert_one_unknown(self, document: dict) -> InsertOneResult | None:
        node_type = document.get("node_type", None)
        if node_type is None: return None
        match node_type:
            case INodeType.FILE.value:
                return self.insert_one(File().from_dict(document))
            case INodeType.DIR.value:
                return self.insert_one(Directory().from_dict(document))
            case INodeType.SYMLINK.value:
                return self.insert_one(Symlink().from_dict(document))
            case INodeType.VIRTUAL.value:
                return None
        return None

    def bulk_write(self, operations: List[Any]) -> BulkWriteResult:
        result = self.collection.bulk_write(operations)
        self.logger.log(f"bulk_write called on collection with {operations=}. {result.acknowledged=}")
        return result

    def find_one(self, filter: dict, projection: dict | None = None):
        result = self.collection.find_one(filter, projection or None)
        self.logger.log(f"find_one called on collection with data: {filter=}; {projection=}. matched={result is not None}")
        return result

    def find(self, filter: Optional[dict] = None, projection: Optional[dict] = None):
        cursor = self.collection.find(filter, projection or None)
        self.logger.log(f"find called on collection with data: {filter=}; {projection=}.")
        return cursor

    def create_index(self, keys: str, is_unique: bool = False):
        self.collection.create_index(keys, unique=is_unique)
        self.logger.log(f"create_index called on collection with {keys=}; {is_unique=}.")

    def delete_one(self, filter: dict) -> bool:
        query = self.collection.delete_one(filter)
        self.logger.log(f"delete_one called on collection with {filter=}; {query.acknowledged=}.")
        return query.acknowledged

    def count_documents(self, filter: dict) -> int:
        query = self.collection.count_documents(filter)
        self.logger.log(f"count_documents called on collection with {filter=}. count={query}")
        return query

    def update_one(self, filter: dict, update: dict) -> bool | None:
        try:
            query = self.collection.update_one(filter, update)
        except DuplicateKeyError:
            self.logger.log(f"update_one called with duplicate {update=}")
            return None
        
        self.logger.log(f"update_one called on collection with {filter=}; {update=}. {query.acknowledged=}")
        return True if query.acknowledged else False

    def exists(self, filter: dict):
        return False if self.find_one(filter) is None else True

class Database:
    instance = None

    def __new__(cls) -> Self:
        if cls.instance is None:
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.logger = Logger("database.master")

        uri = os.environ.get("ME_CONFIG_MONGODB_URL") if bool(os.environ.get("DOCKERIZED", default=0)) else os.environ.get("EDN_MONGODB_URL")
        if uri is None: self.logger.fail("No Mongo URI set. Define EDN_MONGODB_URL in your .env")

        self.client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        self.database = self.client.get_database("edn")
        self.logger.log("Connected to database client")

    def get_collection(self, collection_name: str) -> Collection:
        self.logger.log(f"Requested reference to collection '{collection_name}'")
        return Collection(self.database.get_collection(collection_name))