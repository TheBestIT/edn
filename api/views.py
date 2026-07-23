from typing import Any

from rest_framework.views import APIView
from rest_framework.response import Response

from api.db.auth import Auth
from api.db.models import APIToken, RateLimitResponse
from api.db.cache import Cache
from api.misc.responses import ResponseCodes as code

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
