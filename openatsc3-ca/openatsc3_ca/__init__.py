"""Django project package: settings, URLs, WSGI/ASGI for the OpenATSC3 CA app."""

from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    __version__ = _dist_version("openatsc3-ca")
except PackageNotFoundError:
    __version__ = "0.0.0"
