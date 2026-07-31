from typing import Any, Optional
from rest_framework.views import APIView
from rest_framework.response import Response
from api.db.filesystem import Filesystem
from api.db.models import APIToken, StorageNode, File, INodeType, Permissions, Directory, RateLimitResponse, Symlink, _jsonable
from api.db.auth import Auth
from api.misc.responses import ResponseCodes as code
from api.db.cache import Cache
from api.misc.logger import Logger, LoggerLevel
import tempfile, hashlib, requests, re, datetime
from django.http import StreamingHttpResponse
import json
from typing import List

_UNSAFE = re.compile(r"[^\w.\- ]")

def sanitize_filename(name: str | None) -> str:
    if not name:
        return "blob"
    name = name.replace("\\", "/").split("/")[-1]   # strip any path, incl. ../
    name = _UNSAFE.sub("", name).strip()            # drop control chars, separators
    return name[:255] or "blob"                     # cap length, never empty

def put_file(request, parent_id, filename, token):
    raw = request._request
    hasher = hashlib.sha256()
    fs = Filesystem()
    ratelimit = Cache()
    total_bytes = 0

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
            rate_query = ratelimit.validate_request(token, ratelimit.per_existing_cost)
        else:
            # Ask fs for suitable node
            node: StorageNode | None = fs.get_first_suitable_node(total_bytes)
            if node is None:
                return Response({"status": "No Available Node found"}, code.INSUFFICIENT_STORAGE)

            # Check if Rate-Limit allows operation
            rate_query = ratelimit.validate_request(token, total_bytes*ratelimit.per_byte_cost)
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
            permissions=Permissions(owner=token.token)
        ))

        if db_query == False:
            return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=ratelimit.build_headers(rate_query))

        return Response({"status": "OK"}, code.CREATED, headers=ratelimit.build_headers(rate_query))


def put_dir(request, parent_id, name, token):
    fs = Filesystem()
    ratelimit = Cache()

    rate_query = ratelimit.validate_request(token, ratelimit.per_dir_cost)

    if not rate_query.allowed:
        return Response({"status": "Rate Limited"}, code.LIMITED, headers=ratelimit.build_headers(rate_query))

    query = fs.sign_new_dir(Directory(
        node_type=INodeType.DIR,
        parent_id=parent_id,
        name=name,
        permissions=Permissions(owner=token.token),
        created_at=datetime.datetime.now().timestamp()
    ))

    if query == False:
        if fs.fs_collection.exists({"parent_id": parent_id, "name": name}):
            return Response({"status": "Conflict"}, code.CONFLICT, headers=ratelimit.build_headers(rate_query))
        return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=ratelimit.build_headers(rate_query))

    return Response({"status": "OK"}, code.CREATED, headers=ratelimit.build_headers(rate_query))

def put_symlink(request, parent_id, name, token):
    fs = Filesystem()
    ratelimit = Cache()

    try:
        target = json.loads(request.body).get("target", None)
    except json.JSONDecodeError:
        return Response({"status": "Bad Request"}, code.MALFORMED)

    if target is None: return Response({"status": "Bad Request"}, code.MALFORMED)

    rate_query = ratelimit.validate_request(token, ratelimit.per_symlink_cost)

    if not rate_query.allowed:
        return Response({"status": "Rate Limited"}, code.LIMITED, headers=ratelimit.build_headers(rate_query))

    query = fs.sign_new_symlink(Symlink(
        node_type=INodeType.SYMLINK,
        parent_id=parent_id,
        name=name,
        permissions=Permissions(owner=token.token),
        created_at=datetime.datetime.now().timestamp(),
        target=target
    ))

    if query == False:
        if fs.fs_collection.exists({"parent_id": parent_id, "name": name}):
            return Response({"status": "Conflict"}, code.CONFLICT, headers=ratelimit.build_headers(rate_query))
        return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=ratelimit.build_headers(rate_query))
    
    return Response({"status": "OK"}, code.CREATED, headers=ratelimit.build_headers(rate_query))


def del_file(request, file: File, token: APIToken):
    fs = Filesystem()
    query = fs.fs_collection.delete_one({"_id": file._id})
    if not query: return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR)

    if fs.fs_collection.count_documents({"hashed": file.hashed}) == 0: fs.delete_from_node(file)

    return Response({"status": "OK"}, code.SUCCESS)

def del_symlink(request, symlink: Symlink, token: APIToken):
    fs = Filesystem()
    query = fs.fs_collection.delete_one({"_id": symlink._id})
    return Response({"status": "OK"}, code.SUCCESS) if query else Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR)

def del_directory(request, directory: Directory, token: APIToken):
    fs = Filesystem()

    dir_children = fs.fs_collection.find({"parent_id": directory._id})

    for child in dir_children:
        match child["node_type"]:
            case INodeType.DIR:
                del_directory(request, Directory().from_dict(child), token)
            case INodeType.FILE:
                del_file(request, File().from_dict(child), token)
            case INodeType.SYMLINK:
                del_symlink(request, Symlink().from_dict(child), token)

    return Response({"status": "OK"}, code.SUCCESS) if fs.fs_collection.delete_one({"_id": directory._id}) else Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR)


class TraverseView(APIView):

    def put(self, request, subpath="/"):
        token: APIToken | None = Auth().get_APIToken_from_META_headers(request.META)
        if token is None: return Response({"status": "Bad Request"}, code.MALFORMED)

        edn_mode_header = request.headers.get("X-EDN-Mode", None)
        subpath = subpath.split("/") if subpath[0] == '/' else str(f"/{subpath}").split("/")
        subpath = [item for item in subpath if item != '']

        item_parent_id  = None
        item_name       = sanitize_filename(subpath[-1] if subpath else "")
        item_path       = subpath[:-1]
        item_path       = [sanitize_filename(dir) for dir in item_path]

        if len(subpath) > 1:
            # item has a parent...
            traversed_list = Filesystem().traverse_full_path(item_path)
            if traversed_list is None: return Response({"status": "Bad Path"}, code.MALFORMED)
            item_parent_id = traversed_list[-1:][0]

        match edn_mode_header:
            case None:
                return put_file(request, item_parent_id, item_name, token)
            case "file":
                return put_file(request, item_parent_id, item_name, token)
            case "directory":
                return put_dir(request, item_parent_id, item_name, token)
            case "symlink":
                return put_symlink(request, item_parent_id, item_name, token)
            
        return Response({"status": "Bad X-EDN-Mode Header"}, code.MALFORMED)

    def get(self, request, subpath="/"):
        token: APIToken | None = Auth().get_APIToken_from_META_headers(request.META)
        if token is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        subpath = subpath.split("/") if subpath[0] == '/' else str(f"/{subpath}").split("/")
        subpath = [item for item in subpath if item != '']
        
        item_parent_id  = None
        item_name       = sanitize_filename(subpath[-1] if subpath else "")
        item_path       = subpath[:-1]
        item_path       = [sanitize_filename(dir) for dir in item_path]

        fs = Filesystem()

        if not subpath:
            children = [_jsonable(child) for child in fs.fs_collection.find({"parent_id": None}, {"hosted_node_address": 0})]
            return Response(children, code.SUCCESS)

        if len(subpath) > 1:
            # item has a parent...
            traversed_list = fs.traverse_full_path(item_path)
            if traversed_list is None: return Response({"status": "Bad Path"}, code.MALFORMED)
            item_parent_id = traversed_list[-1:][0]

        item = fs.fs_collection.find_one({"parent_id": item_parent_id, "name": item_name})
        if item is None: return Response({"status": "Not Found"}, code.NOT_FOUND)

        match item["node_type"]:
            case INodeType.DIR:
                directory = Directory().from_dict(item)
                children = [_jsonable(child) for child in fs.fs_collection.find({"parent_id": directory._id}, {"hosted_node_address": 0})]
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

    def head(self, request, subpath="/"):
        token: APIToken | None = Auth().get_APIToken_from_META_headers(request.META)
        if token is None: return Response({"status": "Bad Request"}, code.MALFORMED)
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
            traversed_list = fs.traverse_full_path(item_path)
            if traversed_list is None: return Response({"status": "Bad Path"}, code.MALFORMED)
            item_parent_id = traversed_list[-1:][0]

        item = fs.fs_collection.find_one({"parent_id": item_parent_id, "name": item_name}, {"hosted_node_address": 0})
        if item is None: return Response({"status": "Not Found"}, code.NOT_FOUND)
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

    def delete(self, request, subpath="/"):
        token: APIToken | None = Auth().get_APIToken_from_META_headers(request.META)
        if token is None: return Response({"status": "Bad Request"}, code.MALFORMED)
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
            traversed_list = fs.traverse_full_path(item_path)
            if traversed_list is None: return Response({"status": "Bad Path"}, code.MALFORMED)
            item_parent_id = traversed_list[-1:][0]

        item = fs.fs_collection.find_one({"parent_id": item_parent_id, "name": item_name})
        if item is None: return Response({"status": "Not Found"}, code.NOT_FOUND)
        match item["node_type"]:
            case INodeType.DIR:
                item = Directory().from_dict(item)
                return del_directory(request, item, token)
            case INodeType.FILE:
                item = File().from_dict(item)
                return del_file(request, item, token)
            case INodeType.SYMLINK:
                item = Symlink().from_dict(item)
                return del_symlink(request, item, token)

        return Response({"status": "Bad Request"}, code.MALFORMED)