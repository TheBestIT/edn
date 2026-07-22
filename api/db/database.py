from typing_extensions import Self

import pymongo
from pymongo.collection import Collection as MongoCollection
from pymongo.results import InsertOneResult
import json, os
from api.misc.logger import Logger
from api.db.models import Model

class Collection:
    def __init__(self, collection: MongoCollection) -> None:
        self.logger = Logger(f"database.collection.{collection.name}")
        self.collection = collection

    def insert_one(self, document: Model) -> InsertOneResult:
        result = self.collection.insert_one(document.to_dict())
        document._id = result.inserted_id
        self.logger.log(f"insert_one called on collection with data: {document.to_dict()}. {result.acknowledged=}; {result.inserted_id=}")
        return result


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