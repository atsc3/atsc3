"""WSGI entry point for the OpenATSC3 CA app."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openatsc3_ca.settings")

application = get_wsgi_application()
