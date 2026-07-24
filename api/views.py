from typing import Any

from rest_framework.views import APIView
from rest_framework.response import Response

from api.db.auth import Auth
from api.db.filesystem import Filesystem
from api.db.models import APIToken, StorageNode, File
from api.db.cache import Cache
from api.misc.responses import ResponseCodes as code
from api.misc.logger import Logger

from email.message import Message

import tempfile, hashlib, requests, re, datetime

_UNSAFE = re.compile(r"[^\w.\- ]")

class VersionView(APIView):
    def get(self, request):
        return Response({"version": "0.0.1"}, code.SUCCESS)

# Auth Views
class AuthView_GenAPIToken(APIView):
    def get(self, request):
        result = Auth().generate_api_token()
        if result is None: return Response({"status": "no response from database"}, code.INTERNAL_SERVER_ERROR)
        return Response(result.to_public(), code.CREATED)

# RateLimit Views
class RateLimitView_Test(APIView):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.COST = 40

    def get(self, request):
        token: APIToken | None = Auth().get_APIToken_from_META_headers(request.META)
        if token is None: return Response({"status": "Bad Request"}, code.MALFORMED)
        query = Cache().validate_request(token, self.COST)
        if not query.allowed:
            return Response({"status": "Rate Limited"}, code.LIMITED, headers=Cache().build_headers(query))
            
        return Response({"status": "OK"}, code.SUCCESS, headers=Cache().build_headers(query))

# Upload Views
def sanitize_filename(name: str | None) -> str:
    if not name:
        return "blob"
    name = name.replace("\\", "/").split("/")[-1]   # strip any path, incl. ../
    name = _UNSAFE.sub("", name).strip()            # drop control chars, separators
    return name[:255] or "blob"                     # cap length, never empty


def filename_from_disposition(request) -> str | None:
    m = Message()
    m["Content-Disposition"] = request.headers.get("Content-Disposition", "")
    return m.get_filename()

class UploadView(APIView):

    def put(self, request):
        token: APIToken | None = Auth().get_APIToken_from_META_headers(request.META)
        if token is None: return Response({"status": "Bad Request"}, code.MALFORMED)

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
            if fs.fs_collection.exists({"hashed": blob_hash}): return Response({"status": "OK"}, code.SUCCESS)
            spool.seek(0)

            content_type = request.content_type or "application/octet-stream"
            raw_name = filename_from_disposition(request)
            safe_name = sanitize_filename(raw_name)

            # Ask fs for suitable node
            node: StorageNode | None = fs.get_first_suitable_node(total_bytes)
            if node is None:
                return Response({"status": "No Available Node found"}, code.INSUFFICIENT_STORAGE)

            # Check if Rate-Limit allows operation
            rate_query = ratelimit.validate_request(token, total_bytes*ratelimit.per_byte_cost)
            if not rate_query.allowed:
                return Response({"status": "Rate Limited"}, code.LIMITED, headers=ratelimit.build_headers(rate_query))

            Logger(f"upload:${blob_hash}").log(f"Calling PUT to node @{node.address}:{node.port} of file of COST={total_bytes*ratelimit.per_byte_cost} ({total_bytes=} at {ratelimit.per_byte_cost} tokens/byte)")

            # Allow operation and passthrough blob
            node_request = requests.put(f"http://{node.address}:{node.port}/blob/{blob_hash}", data=spool)
            if node_request.status_code != 200 and node_request.status_code != 201:
                return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=ratelimit.build_headers(rate_query))

            db_query = fs.sign_new_file(File(
                hashed=blob_hash,
                content_type=content_type,
                filename=safe_name,
                size=total_bytes,
                hosted_node_address=node.address,
                created_at=datetime.datetime.now().timestamp()
            ))

            if db_query == False:
                return Response({"status": "Internal Server Error"}, code.INTERNAL_SERVER_ERROR, headers=ratelimit.build_headers(rate_query))

            return Response({"status": "OK"}, code.CREATED, headers=ratelimit.build_headers(rate_query))