"""ASGI entry point for the OpenATSC3 CA app."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openatsc3_ca.settings")

application = get_asgi_application()
