import os

from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self) -> None:
        if os.environ.get("RUN_MAIN") != "true":
            return

        from api import Scheduler
        Scheduler()
