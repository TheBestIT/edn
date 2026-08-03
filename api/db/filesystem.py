from typing_extensions import Self
from pymongo import ReplaceOne
from bson import ObjectId
import requests, datetime

from api.db.database import Database
from api.misc.logger import Logger, LoggerLevel
from api.db.models import StorageNode, NodeStatus, File, Directory, INodeType, Symlink, TreeNode

from typing import List, Optional

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

        self.logger = Logger("database.filesystem", LoggerLevel.DATABASE)
        self.fs_collection = Database().get_collection("filesystem")
        self.nodes_collection = Database().get_collection("nodes")
        self.max_blob_size = 8 * 1024 * 1024 # 8GB

        self.nodes_collection.create_index("address", is_unique=True)
        self.fs_collection.collection.create_index([("parent_id", 1), ("name", 1)], unique=True)
        self.fs_collection.collection.create_index("hashed", partialFilterExpression={"hashed": {"$exists": True}})

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

    def get_node_from_address(self, address: str) -> StorageNode | None:
        for node in self.cached_nodes:
            if node.address == address and node.health.status == NodeStatus.ALIVE:
                return node

        return None

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

    def sign_new_dir(self, dir: Directory) -> bool:
        query = self.fs_collection.insert_one(dir)
        self.logger.log(f"Attempted to sign new Directory ({dir.name}) in the db. query={query.acknowledged if query is not None else 'None'}")

        if query is None: return False
        return query.acknowledged

    def sign_new_symlink(self, symlink: Symlink) -> bool:
        query = self.fs_collection.insert_one(symlink)
        self.logger.log(f"Attempted to sign new Symlink ({symlink.name} -> {symlink.target}) in the db. query={query.acknowledged if query is not None else 'None'}")
        
        if query is None: return False
        return query.acknowledged

    def get_child_dir_from_folder(self, origin: ObjectId | None, child_name: str) -> Directory | None:
        query = self.fs_collection.find_one({"parent_id": origin, "name": child_name, "node_type": INodeType.DIR})
        return Directory().from_dict(query) if query is not None else None

    def delete_from_node(self, file: File) -> bool:
        if file.hosted_node_address is None: return False
        hosting_node = self.get_node_from_address(file.hosted_node_address)
        file_hash = file.hashed
        
        if hosting_node is None: return False

        query = requests.delete(f"http://{hosting_node.address}:{hosting_node.port}/blob/{file_hash}")

        self.logger.log(f"Querying deletion of {file_hash=} from Node@{hosting_node.address}:{hosting_node.port}. {query.status_code=}")
        
        return True if query.status_code == 200 else False
    
    # Returns the traversed path in the order of the given path
    def traverse_full_path(self, path: List[str]) -> List[ObjectId] | None:
        last_dir: Optional[Directory] = None
        traversed_list = []
        for dir in path:
            if last_dir is None:
                query = self.get_child_dir_from_folder(None, dir)
                if query is None: return None # broken tree chain
                last_dir = query
                traversed_list.append(query._id)
                continue
            query = last_dir.get_child_dir(dir)
            if query is None: return None
            traversed_list.append(query._id)
            last_dir = query
            
        return traversed_list

    def index_dir(self, directory: Directory, strip_oids: bool = False) -> TreeNode:
        children = self.fs_collection.find({"parent_id": directory._id})
        if strip_oids:
            directory._id = None
            directory.parent_id = None
        node: TreeNode = TreeNode(iNode=directory)
        node.children = []

        for child in children:
            match child["node_type"]:
                case INodeType.FILE.value:
                    file = File().from_dict(child)
                    if strip_oids:
                        file._id = None
                        file.parent_id = None
                    node.children.append(TreeNode(iNode=file))
                case INodeType.SYMLINK.value:
                    symlink = Symlink().from_dict(child)
                    if strip_oids:
                        symlink._id = None
                        symlink.parent_id = None
                    node.children.append(TreeNode(iNode=symlink))
                case INodeType.DIR.value:
                    dir = Directory().from_dict(child)
                    node.children.append(self.index_dir(dir, strip_oids))

        return node

    def repopulate_parent_ids_from_treenode(self, treenode: TreeNode):
        node_master_dir: Directory = Directory().from_dict(treenode.iNode.to_dict())
        if treenode.children is None: return
        for child in treenode.children:
            match child.iNode.node_type:
                case INodeType.FILE.value:
                    file = File().from_dict(child.iNode.to_dict())
                    file.parent_id = node_master_dir._id
                    file.created_at = datetime.datetime.now().timestamp()
                    query = self.fs_collection.insert_one(file)
                    if query is None: return
                    if not query.acknowledged: return
                    file._id = query.inserted_id
                    child.iNode = file
                case INodeType.SYMLINK.value:
                    symlink = Symlink().from_dict(child.iNode.to_dict())
                    symlink.parent_id = node_master_dir._id
                    symlink.created_at = datetime.datetime.now().timestamp()
                    query = self.fs_collection.insert_one(symlink)
                    if query is None: return
                    if not query.acknowledged: return
                    symlink._id = query.inserted_id
                    child.iNode = symlink
                case INodeType.DIR.value:
                    dir = Directory().from_dict(child.iNode.to_dict())
                    dir.parent_id = node_master_dir._id
                    dir.created_at = datetime.datetime.now().timestamp()
                    query = self.fs_collection.insert_one(dir)
                    if query is None: return
                    if not query.acknowledged: return
                    dir._id = query.inserted_id
                    child.iNode = dir
                    self.repopulate_parent_ids_from_treenode(child)


    def copy_item(self, item: dict, target_oid: ObjectId | None) -> bool | None:
        if item["node_type"] != INodeType.DIR.value:
            item["parent_id"] = target_oid
            item["created_at"] = datetime.datetime.now().timestamp()
            del item["_id"]
            insert_query = self.fs_collection.insert_one_unknown(item)
            return None if insert_query is None else insert_query.acknowledged

        # we need to rebuild the full tree of the directory and copy it over
        target_directory = Directory().from_dict(item)
        target_directory.created_at = datetime.datetime.now().timestamp()
        dir_tree = self.index_dir(target_directory, True)
        self.logger.log(f"self.index_dir(target_directory, True) -> {dir_tree=}")

        query = self.fs_collection.insert_one(dir_tree.iNode)
        if query is None: return
        if not query.acknowledged: return
        dir_tree.iNode._id = query.inserted_id
        dir_tree.iNode.parent_id = target_oid
        self.repopulate_parent_ids_from_treenode(dir_tree)
        self.logger.log(f"self.repopulate_parent_ids_from_treenode(dir_tree) -> {dir_tree=}")

        return True

