"""App config for the CA catalog."""

from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "openatsc3_ca.catalog"
    label = "catalog"
    verbose_name = "CA catalog"
