from rest_framework.views import APIView
from rest_framework.response import Response

# Create your views here.

class VersionView(APIView):
    def get(self, request):
        return Response({"version": "0.0.1"}, 200)