from typing_extensions import Self
from pymongo import ReplaceOne

from api.db.database import Database
from api.misc.logger import Logger
from api.db.models import StorageNode, NodeStatus, File

from typing import List

class Filesystem:
    instance = None

    def __new__(cls) -> Self:
        if cls.instance == None:
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.logger = Logger("database.filesystem")
        self.fs_collection = Database().get_collection("filesystem")
        self.nodes_collection = Database().get_collection("nodes")
        self.max_blob_size = 8 * 1024 * 1024 # 8GB

        self.nodes_collection.create_index("address", is_unique=True)
        self.fs_collection.create_index("hashed", is_unique=True)

        self.cached_nodes: List[StorageNode] = []
        cursor = self.nodes_collection.find({}) # Get all nodes
        for node in cursor:
            self.cached_nodes.append(StorageNode(address="").from_dict(node))

    def check_nodes(self):
        for node in self.cached_nodes: node.check_health()

        self.nodes_collection.bulk_write([
            ReplaceOne({"_id": node._id}, node.to_dict(), upsert=True)
            for node in self.cached_nodes
        ])
        

    def sign_new_node(self, node: StorageNode) -> bool:
        node.check_health()
        if node.health.status == NodeStatus.DEAD:
            return False

        query = self.nodes_collection.insert_one(node)
        if query is None: 
            self.logger.log(f"node @{node.address}:{node.port} already exists in db")
            return False

        self.cached_nodes.append(node)

        return True if query.acknowledged else False

    def get_first_suitable_node(self, blob_byte_size: int) -> StorageNode | None:
        target_node: StorageNode | None = None

        for node in self.cached_nodes:
            if node.health.status != NodeStatus.ALIVE: continue
            if node.health.store.writable != True: continue
            if node.health.store.available_bytes <= blob_byte_size: continue

            if target_node == None: target_node = node
            elif target_node.health.store.used_percent > node.health.store.used_percent: target_node = node

        return target_node

    def sign_new_file(self, file: File) -> bool:
        query = self.fs_collection.insert_one(file)
        self.logger.log(f"Attempted to sign new File ({file.hashed}) in the db. query={query.acknowledged if query is not None else 'None'}")

        if query is None: return False
        return query.acknowledged