from typing import Any

from rest_framework.views import APIView
from rest_framework.response import Response

from api.db.auth import Auth
from api.misc.responses import ResponseCodes as code


class VersionView(APIView):
    def get(self, request):
        return Response({"version": "0.0.1"}, code.SUCCESS)

# Auth Views
class AuthView_GenUser(APIView):
    def get(self, request):
        return Response(status=code.FORBIDDEN)
        result = Auth().generate_api_token()
        if result is None: return Response({"status": "no response from database"}, code.INTERNAL_SERVER_ERROR)
        return Response(result.to_public(), code.CREATED)

# RateLimit Views
class RateLimitView_Test(APIView):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.COST = 40

    def get(self, request):
        return Response({"status": "OK"}, code.SUCCESS)

