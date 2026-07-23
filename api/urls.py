from django.urls import path
from .views import VersionView, AuthView_GenAPIToken, RateLimitView_Test


urlpatterns = [
    path('version/', VersionView.as_view()),
    path("auth/generate_api_token/", AuthView_GenAPIToken.as_view()),
    path("ratelimit/test/", RateLimitView_Test.as_view())
]