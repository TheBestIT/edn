from rest_framework.views import APIView
from rest_framework.response import Response

from api.db.auth import Auth
from api.db.models import APIToken
from api.misc.responses import ResponseCodes as code

class VersionView(APIView):
    def get(self, request):
        return Response({"version": "0.0.1"}, 200)

# Auth Views
class AuthView_GenAPIToken(APIView):
    def get(self, request):
        result = Auth().generate_api_token()
        if result is None: return Response({"error": "no response from database"}, code.INTERNAL_SERVER_ERROR)
        return Response(result.to_public(), code.CREATED)