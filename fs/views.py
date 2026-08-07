from typing import Optional
from rest_framework.views import APIView
from rest_framework.response import Response
from api.db.filesystem import Filesystem
from api.db.models import User, StorageNode, File, INodeType, Permissions, Directory, PermissionFlags, RateLimitResponse, Symlink, _jsonable
from api.db.auth import Auth
from api.db.userinteractions import *
from api.misc.responses import ResponseCodes as code
from api.db.cache import Cache
from api.misc.logger import Logger, LoggerLevel
import tempfile, hashlib, requests, re, datetime
from django.http import StreamingHttpResponse
import json
from bson import ObjectId
from typing import List

_UNSAFE = re.compile(r"[^\w.\- ]")

def sanitize_filename(name: str | None) -> str:
    if not name:
        return "blob"
    name = name.replace("\\", "/").split("/")[-1]   # strip any path, incl. ../
    name = _UNSAFE.sub("", name).strip()            # drop control chars, separators
    return name[:255] or "blob"                     # cap length, never empty

def put_file(request, parent_id, filename, user: User):
    raw = request._request
    hasher = hashlib.sha256()
    fs = Filesystem()
    ratelimit = Cache()
    total_bytes = 0

    if user._id is None: return Response({"status": "Bad Request"}, code.MALFORMED)
    if user.groups is None: return Response({"status": "Bad Request"}, code.MALFORMED)

    permissions = Permissions(
        owner=(user._id, PermissionFlags.READ | PermissionFlags.WRITE),
        group=(user.groups[0], PermissionFlags.READ | PermissionFlags.WRITE)
    )

    with tempfile.SpooledTemporaryFile(max_size=fs.max_blob_size) as spool:
        while True:
            chunk = raw.read(8192)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > fs.max_blob_size:
                return Response({"status": "Content Too Large"}, code.TOO_LARGE)
            hasher.update(chunk)
            spool.write(chunk)
        
        blob_hash = hasher.hexdigest()
        spool.seek(0)

        content_type = request.content_type or "application/octet-stream"
        node_address = ""
        rate_query: Optional[RateLimitResponse] = None

        existing_entry = fs.fs_collection.find_one({"hashed": blob_hash})
        if existing_entry is not None:
            existing_entry = File().from_dict(existing_entry)
            if existing_entry.parent_id == parent_id and existing_entry.name == filename: return Response({"status": "OK"}, code.SUCCESS)
            node_address = existing_entry.hosted_node_address
            rate_query = ratelimit.validate_request(user, ratelimit.per_existing_cost)
        else:
            # Ask fs for suitable node
            node: StorageNode | None = fs.get_first_suitable_node(total_bytes)
            if node is None:
                return Response({"status": "No Available Node found"}, code.INSUFFICIENT_STORAGE)

            # Check if Rate-Limit allows operation
            rate_query = ratelimit.validate_request(user, total_bytes*ratelimit.per_byte_cost)
            if not rate_query.allowed:
                return Response({"status": "Rate Limited"}, code.LIMITED, headers=ratelimit.build_headers(rate_query))

            Logger(f"upload:${blob_hash}", LoggerLevel.ENDPOINT).log(f"Calling PUT to node @{node.address}:{node.port} of file of COST={total_bytes*ratelimit.per_byte_cost} ({total_bytes=} at {ratelimit.per_byte_cost} tokens/byte)")

            # Allow operation and passthrough blob
            node_request = requests.put(f"http://{node.address}:{node.port}/blob/{blob_hash}", data=spool)
            if node_request.status_code != 200 and node_request.status_code != 201:
                return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=ratelimit.build_headers(rate_query))

            node_address = node.address

        db_query = fs.sign_new_file(File(
            node_type=INodeType.FILE,
            parent_id=parent_id,
            hashed=blob_hash,
            content_type=content_type,
            name=filename,
            size=total_bytes,
            hosted_node_address=node_address,
            created_at=datetime.datetime.now().timestamp(),
            permissions=permissions
        ))

        if db_query == False:
            return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=ratelimit.build_headers(rate_query))

        return Response({"status": "OK"}, code.CREATED, headers=ratelimit.build_headers(rate_query))


def put_dir(request, parent_id, name, user: User):
    fs = Filesystem()
    ratelimit = Cache()

    if user._id is None: return Response({"status": "Bad Request"}, code.MALFORMED)
    if user.groups is None: return Response({"status": "Bad Request"}, code.MALFORMED)

    permissions = Permissions(
        owner=(user._id, PermissionFlags.READ | PermissionFlags.WRITE),
        group=(user.groups[0], PermissionFlags.READ | PermissionFlags.WRITE)
    )

    rate_query = ratelimit.validate_request(user, ratelimit.per_dir_cost)

    if not rate_query.allowed:
        return Response({"status": "Rate Limited"}, code.LIMITED, headers=ratelimit.build_headers(rate_query))

    query = fs.sign_new_dir(Directory(
        node_type=INodeType.DIR,
        parent_id=parent_id,
        name=name,
        permissions=permissions,
        created_at=datetime.datetime.now().timestamp()
    ))

    if query == False:
        if fs.fs_collection.exists({"parent_id": parent_id, "name": name}):
            return Response({"status": "Conflict"}, code.CONFLICT, headers=ratelimit.build_headers(rate_query))
        return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=ratelimit.build_headers(rate_query))

    return Response({"status": "OK"}, code.CREATED, headers=ratelimit.build_headers(rate_query))

def put_symlink(request, parent_id, name, user: User):
    fs = Filesystem()
    ratelimit = Cache()

    if user._id is None: return Response({"status": "Bad Request"}, code.MALFORMED)
    if user.groups is None: return Response({"status": "Bad Request"}, code.MALFORMED)

    permissions = Permissions(
        owner=(user._id, PermissionFlags.READ | PermissionFlags.WRITE),
        group=(user.groups[0], PermissionFlags.READ | PermissionFlags.WRITE)
    )

    try:
        target = json.loads(request.body).get("target", None)
    except json.JSONDecodeError:
        return Response({"status": "Bad Request"}, code.MALFORMED)

    if target is None: return Response({"status": "Bad Request"}, code.MALFORMED)

    rate_query = ratelimit.validate_request(user, ratelimit.per_symlink_cost)

    if not rate_query.allowed:
        return Response({"status": "Rate Limited"}, code.LIMITED, headers=ratelimit.build_headers(rate_query))

    query = fs.sign_new_symlink(Symlink(
        node_type=INodeType.SYMLINK,
        parent_id=parent_id,
        name=name,
        permissions=permissions,
        created_at=datetime.datetime.now().timestamp(),
        target=target
    ))

    if query == False:
        if fs.fs_collection.exists({"parent_id": parent_id, "name": name}):
            return Response({"status": "Conflict"}, code.CONFLICT, headers=ratelimit.build_headers(rate_query))
        return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=ratelimit.build_headers(rate_query))
    
    return Response({"status": "OK"}, code.CREATED, headers=ratelimit.build_headers(rate_query))


def del_file(request, file: File, user: User):
    fs = Filesystem()
    query = fs.fs_collection.delete_one({"_id": file._id})
    if not query: return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR)

    if fs.fs_collection.count_documents({"hashed": file.hashed}) == 0: fs.delete_from_node(file)

    return Response({"status": "OK"}, code.SUCCESS)

def del_symlink(request, symlink: Symlink, user: User):
    fs = Filesystem()
    query = fs.fs_collection.delete_one({"_id": symlink._id})
    return Response({"status": "OK"}, code.SUCCESS) if query else Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR)

def del_directory(request, directory: Directory, user: User):
    fs = Filesystem()

    dir_children = fs.fs_collection.find({"parent_id": directory._id})

    for child in dir_children:
        match child["node_type"]:
            case INodeType.DIR:
                del_directory(request, Directory().from_dict(child), user)
            case INodeType.FILE:
                del_file(request, File().from_dict(child), user)
            case INodeType.SYMLINK:
                del_symlink(request, Symlink().from_dict(child), user)

    return Response({"status": "OK"}, code.SUCCESS) if fs.fs_collection.delete_one({"_id": directory._id}) else Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR)

class TraverseView(APIView):
    # TODO: make common function for all the token verification/path handling
    def put(self, request, subpath="/"): # PermissionFlags.WRITE
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)

        edn_mode_header = request.headers.get("X-EDN-Mode", None)
        subpath = subpath.split("/") if subpath[0] == '/' else str(f"/{subpath}").split("/")
        subpath = [item for item in subpath if item != '']

        item_parent_id  = None
        item_name       = sanitize_filename(subpath[-1] if subpath else "")
        item_path       = subpath[:-1]
        item_path       = [sanitize_filename(dir) for dir in item_path]

        if len(subpath) > 1:
            # item has a parent...
            # This only checks for the PermissionFlags.READ up to the directory where the item_parent_id is stored
            traversed_list = UserInteractions(user).traverseFullPath(item_path)
            if isinstance(traversed_list, InteractionResponse):
                return Response({"status": traversed_list.message}, code.MALFORMED if traversed_list.status == InteractionResponseCodes.NONE else code.FORBIDDEN)
            item_parent_id = traversed_list[-1:][0]

        fs = Filesystem()
        
        parent_object = fs.fs_collection.find_one({"_id": item_parent_id})
        if parent_object is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        flag_query = UserInteractions(user).checkUnknownFlag(parent_object, PermissionFlags.WRITE)
        if not flag_query: return Response({"status": f"Permission Denied: {parent_object['name']}"}, code.FORBIDDEN)

        match edn_mode_header:
            case None:
                return put_file(request, item_parent_id, item_name, user)
            case "file":
                return put_file(request, item_parent_id, item_name, user)
            case "directory":
                return put_dir(request, item_parent_id, item_name, user)
            case "symlink":
                return put_symlink(request, item_parent_id, item_name, user)
            
        return Response({"status": "Bad X-EDN-Mode Header"}, code.MALFORMED)

    def get(self, request, subpath="/"): # PermissionFlags.READ
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        subpath = subpath.split("/") if subpath[0] == '/' else str(f"/{subpath}").split("/")
        subpath = [item for item in subpath if item != '']
        
        item_parent_id  = None
        item_name       = sanitize_filename(subpath[-1] if subpath else "")
        item_path       = subpath[:-1]
        item_path       = [sanitize_filename(dir) for dir in item_path]

        fs = Filesystem()

        if not subpath:
            children = [_jsonable(child) for child in fs.fs_collection.find({"parent_id": None, "_id": { "$ne": None } }, {"hosted_node_address": 0})]
            return Response(children, code.SUCCESS)

        if len(subpath) > 1:
            # item has a parent...
            traversed_list = UserInteractions(user).traverseFullPath(item_path)
            if isinstance(traversed_list, InteractionResponse):
                return Response({"status": traversed_list.message}, code.MALFORMED if traversed_list.status == InteractionResponseCodes.NONE else code.FORBIDDEN)
            item_parent_id = traversed_list[-1:][0]

        item = fs.fs_collection.find_one({"parent_id": item_parent_id, "name": item_name})
        if item is None: return Response({"status": "Not Found"}, code.NOT_FOUND)

        flag_query = UserInteractions(user).checkUnknownFlag(item, PermissionFlags.READ)
        if not flag_query: return Response({"status": f"Permission Denied: {item['name']}"}, code.FORBIDDEN)

        match item["node_type"]:
            case INodeType.DIR:
                directory = Directory().from_dict(item)
                children = [_jsonable(child) for child in fs.fs_collection.find({"parent_id": directory._id, "_id": { "$ne": None } }, {"hosted_node_address": 0})]
                
                return Response(children, code.SUCCESS)
            case INodeType.FILE:
                file = File().from_dict(item)
                if file.hosted_node_address is None: return Response({"status": "Resource Unavailable"}, code.UNAVAILABLE) 
                storage_node = fs.get_node_from_address(file.hosted_node_address)
                if storage_node is None: return Response({"status": "Resource Unavailable"}, code.UNAVAILABLE)
                node_url = f"http://{storage_node.address}:{storage_node.port}/blob/{file.hashed}"
                params = {"ct": file.content_type, "fn": file.name}

                fwd_headers = {}
                if "HTTP_RANGE" in request.META:
                    fwd_headers["Range"] = request.META["HTTP_RANGE"]

                upstream = requests.get(
                    node_url, params=params, headers=fwd_headers,
                    stream=True, timeout=(5, None),
                )

                if upstream.status_code == 404:
                    upstream.close()
                    return Response({"status": "Not Found"}, code.NOT_FOUND)

                def body():
                    try:
                        for chunk in upstream.iter_content(64 * 1024):
                            yield chunk
                    finally:
                        upstream.close()

                resp = StreamingHttpResponse(
                    body(),
                    status=upstream.status_code,                      # 200 or 206
                    content_type=upstream.headers.get("Content-Type", "application/octet-stream"),
                )
                for h in ("Content-Length", "Content-Range", "ETag",
                        "Content-Disposition", "Accept-Ranges", "Cache-Control"):
                    if h in upstream.headers:
                        resp[h] = upstream.headers[h]
                return resp
            case INodeType.SYMLINK:
                symlink = Symlink().from_dict(item)
                if symlink.target is None: return Response({"status": "Bad Symlink Target"}, code.MALFORMED)
                return self.get(request, subpath=symlink.target)

        return Response({"status": "Bad Request"}, code.MALFORMED)

    def head(self, request, subpath="/"): # PermissionFlags.READ
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        subpath = subpath.split("/") if subpath[0] == '/' else str(f"/{subpath}").split("/")
        subpath = [item for item in subpath if item != '']
        
        item_parent_id  = None
        item_name       = sanitize_filename(subpath[-1] if subpath else "")
        item_path       = subpath[:-1]
        item_path       = [sanitize_filename(dir) for dir in item_path]

        fs = Filesystem()

        if not subpath:
            return Response({"status": "Forbidden"}, code.FORBIDDEN)

        if len(subpath) > 1:
            # item has a parent...
            # This only checks for the PermissionFlags.READ up to the directory where the item_parent_id is stored
            traversed_list = UserInteractions(user).traverseFullPath(item_path)
            if isinstance(traversed_list, InteractionResponse):
                return Response({"status": traversed_list.message}, code.MALFORMED if traversed_list.status == InteractionResponseCodes.NONE else code.FORBIDDEN)
            item_parent_id = traversed_list[-1:][0]

        item = fs.fs_collection.find_one({"parent_id": item_parent_id, "name": item_name}, {"hosted_node_address": 0})
        if item is None: return Response({"status": "Not Found"}, code.NOT_FOUND)

        flag_query = UserInteractions(user).checkUnknownFlag(item, PermissionFlags.READ)
        if not flag_query: return Response({"status": f"Permission Denied: {item['name']}"}, code.FORBIDDEN)

        headers = {}
        match item["node_type"]:
            case INodeType.DIR:
                item = Directory().from_dict(item)
                headers["X-EDN-Mode"] = "directory"
            case INodeType.FILE:
                item = File().from_dict(item)
                headers["X-EDN-Mode"] = "file"
                headers["Content-Size"] = item.size
                headers["X-Content-Type"] = item.content_type
            case INodeType.SYMLINK:
                item = Symlink().from_dict(item)
                headers["X-EDN-Mode"] = "symlink"
                headers["X-EDN-SymlinkTarget"] = item.target

        return Response(status=code.SUCCESS, headers=headers)

    def delete(self, request, subpath="/"): # PermissionFlags.WRITE
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        subpath = subpath.split("/") if subpath[0] == '/' else str(f"/{subpath}").split("/")
        subpath = [item for item in subpath if item != '']
        
        item_parent_id  = None
        item_name       = sanitize_filename(subpath[-1] if subpath else "")
        item_path       = subpath[:-1]
        item_path       = [sanitize_filename(dir) for dir in item_path]

        fs = Filesystem()

        if not subpath:
            return Response({"status": "Forbidden"}, code.FORBIDDEN)

        if len(subpath) > 1:
            # item has a parent...
            # This only checks for the PermissionFlags.READ up to the directory where the item_parent_id is stored
            traversed_list = UserInteractions(user).traverseFullPath(item_path)
            if isinstance(traversed_list, InteractionResponse):
                return Response({"status": traversed_list.message}, code.MALFORMED if traversed_list.status == InteractionResponseCodes.NONE else code.FORBIDDEN)
            item_parent_id = traversed_list[-1:][0]

        item = fs.fs_collection.find_one({"parent_id": item_parent_id, "name": item_name})
        if item is None: return Response({"status": "Not Found"}, code.NOT_FOUND)

        flag_query = UserInteractions(user).checkUnknownFlag(item, PermissionFlags.WRITE)
        if not flag_query: return Response({"status": f"Permission Denied: {item['name']}"}, code.FORBIDDEN)

        match item["node_type"]:
            case INodeType.DIR:
                item = Directory().from_dict(item)
                return del_directory(request, item, user)
            case INodeType.FILE:
                item = File().from_dict(item)
                return del_file(request, item, user)
            case INodeType.SYMLINK:
                item = Symlink().from_dict(item)
                return del_symlink(request, item, user)

        return Response({"status": "Bad Request"}, code.MALFORMED)

    def patch(self, request, subpath="/"): # PermissionFlags.WRITE
        user: User | None = Auth().get_User_from_META_headers(request.META)
        if user is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        subpath = subpath.split("/") if subpath[0] == '/' else str(f"/{subpath}").split("/")
        subpath = [item for item in subpath if item != '']
        
        item_parent_id  = None
        item_name       = sanitize_filename(subpath[-1] if subpath else "")
        item_path       = subpath[:-1]
        item_path       = [sanitize_filename(dir) for dir in item_path]

        fs = Filesystem()

        if not subpath:
            return Response({"status": "Forbidden"}, code.FORBIDDEN)

        if len(subpath) > 1:
            # item has a parent...
            # This only checks for the PermissionFlags.READ up to the directory where the item_parent_id is stored
            traversed_list = UserInteractions(user).traverseFullPath(item_path)
            if isinstance(traversed_list, InteractionResponse):
                return Response({"status": traversed_list.message}, code.MALFORMED if traversed_list.status == InteractionResponseCodes.NONE else code.FORBIDDEN)
            item_parent_id = traversed_list[-1:][0]

        target_item = fs.fs_collection.find_one({"parent_id": item_parent_id, "name": item_name}, {"hosted_node_address": 0})
        if target_item is None: return Response({"status": "Not Found"}, code.NOT_FOUND)
        flag_query = UserInteractions(user).checkUnknownFlag(target_item, PermissionFlags.READ)
        if not flag_query: return Response({"status": f"Can't open file '{target_item['name']}': Permission Denied"}, code.FORBIDDEN)

        operation = request.headers.get("X-EDN-Patch", None)
        if operation is None: return Response({"status": "Missing X-EDN-Patch Header"}, code.MALFORMED)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return Response({"status": "Bad Request"}, code.MALFORMED)

        path = data.get("path", None)
        name = data.get("name", None)
        target_dir_oid: Optional[ObjectId] = None

        if path is not None:
            path = path.split("/") if path[0] == '/' else str(f"/{path}").split("/")
            path = [item for item in path if item != '']
            
            item_parent_id  = None
            item_name       = sanitize_filename(path[-1] if path else "")
            item_path       = path[:-1]
            item_path       = [sanitize_filename(dir) for dir in item_path]

            if len(path) > 1:
                # item has a parent...
                # This only checks for the PermissionFlags.READ up to the directory where the item_parent_id is stored
                traversed_list = UserInteractions(user).traverseFullPath(item_path)
                if isinstance(traversed_list, InteractionResponse):
                    return Response({"status": traversed_list.message}, code.MALFORMED if traversed_list.status == InteractionResponseCodes.NONE else code.FORBIDDEN)
                item_parent_id = traversed_list[-1:][0]

                item = fs.fs_collection.find_one({"parent_id": item_parent_id, "name": item_name}, {"hosted_node_address": 0})
                if item is None: return Response({"status": "Target directory not found"}, code.NOT_FOUND)

                if item["node_type"] != INodeType.DIR: return Response({"status": "Target is not a directory"}, code.MALFORMED)
                target_dir_oid = item["_id"]

        target_dir = fs.fs_collection.find_one({"_id": target_dir_oid})
        if target_dir is None: return Response({"status": "Not Found"}, code.NOT_FOUND)
        readflag_query = UserInteractions(user).checkUnknownFlag(target_dir, PermissionFlags.READ)
        writeflag_query = UserInteractions(user).checkUnknownFlag(target_dir, PermissionFlags.WRITE)
        if not readflag_query or not writeflag_query: return Response({"status": f"Permission Denied: {target_dir['name']}"}, code.FORBIDDEN)

        query: Optional[bool] = None
        match operation:
            case "move":
                if path is None: return Response({"status": "Bad Request"}, code.MALFORMED)
                if target_item["_id"] in fs.traverse_full_path(path): return Response({"status": "Conflict"}, code.CONFLICT)
                if target_item["parent_id"] == target_dir_oid: return Response({"status": "ok"}, code.SUCCESS)
                query = fs.fs_collection.update_one({"_id": target_item["_id"]}, {"$set": {"parent_id": target_dir_oid}})
            case "rename":
                if name is None: return Response({"status": "Bad Request"}, code.MALFORMED)
                name = sanitize_filename(name)
                if not UserInteractions(user).checkUnknownFlag(target_item, PermissionFlags.WRITE): return Response({"status": f"Permission Denied: {target_item['name']}"}, code.FORBIDDEN)
                update_query = fs.fs_collection.update_one({"_id": target_item["_id"]}, {"$set": {"name": name}})
                if update_query is None: return Response({"status": "Conflict"}, code.CONFLICT)
                query = update_query
            case "copy":
                if path is None: return Response({"status": "Bad Request"}, code.MALFORMED)
                if target_item["parent_id"] == target_dir_oid: return Response({"status": "Conflict"}, code.CONFLICT)
                copy_query = fs.copy_item(target_item, target_dir_oid)
                if copy_query is None: return Response({"status": "ok"}, code.SUCCESS)
                query = copy_query

        if query is None: return Response({"status": "Invalid X-EDN-Patch Header"}, code.MALFORMED)

        return Response({"status": "ok"}, code.SUCCESS) if query else Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR)