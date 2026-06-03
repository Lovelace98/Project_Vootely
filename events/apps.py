from django.apps import AppConfig


class EventsConfig(AppConfig):
    name = 'events'

    def ready(self):
        from votecentral import checks  # noqa: F401
        from . import signals  # noqa: F401
