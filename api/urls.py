from django.urls import path
from .views import VersionView


urlpatterns = [
    path('version/', VersionView.as_view(), name='version'),
]