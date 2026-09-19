from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"

    def ready(self) -> None:
        from . import signals  # noqa: F401
        from .scheduler.engine import scheduler_should_run, start_scheduler

        if scheduler_should_run():
            start_scheduler()
