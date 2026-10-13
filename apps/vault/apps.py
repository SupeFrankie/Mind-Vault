from django.apps import AppConfig


class VaultConfig(AppConfig):
    name = "apps.vault"
    label = "vault"

    def ready(self):
        from . import signals  # noqa: F401
