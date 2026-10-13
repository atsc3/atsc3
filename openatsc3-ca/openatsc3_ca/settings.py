"""Django settings for the OpenATSC3 CA management app.

The **database is the source of truth** for certificate lifecycle state.  The
private keys stay on disk (0600) and are referenced by path + fingerprint; the
PEM tree under ``OPENATSC3_CA_BASE_DIR`` is a *materialized view* written by the
``export`` command and read by the receiver/signer.

Database selection, in order:

1. ``OPENATSC3_CA_DATABASE_URL`` — an explicit postgres URL (deployment, CI);
2. ``DATABASE_URL`` — a conventional fallback;
3. otherwise a **rootless embedded PostgreSQL** (``pgserver``, no Docker).

Run the app on Python 3.12 with the ``[test]`` extra installed.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parents[1]


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get(
    "OPENATSC3_CA_SECRET_KEY",
    "django-insecure-dev-only-openatsc3-ca-change-me",
)
DEBUG = _flag("OPENATSC3_CA_DEBUG", True)
ALLOWED_HOSTS = [
    h for h in os.environ.get("OPENATSC3_CA_ALLOWED_HOSTS", "*").split(",") if h
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "openatsc3_ca.catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "openatsc3_ca.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "openatsc3_ca.wsgi.application"
ASGI_APPLICATION = "openatsc3_ca.asgi.application"


def _parse_database_url(url: str) -> dict:
    """Parse a ``postgres://`` / ``postgresql://`` URL into a Django DB config.

    Handles the two forms the workspace uses: a TCP server (host + port) and a
    Unix-socket server (``?host=/path``, as returned by the embedded
    PostgreSQL).  An empty host means the local socket directory.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"unsupported database scheme {parsed.scheme!r}")
    query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
    host = query.get("host", parsed.hostname or "")
    config = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")) or "postgres",
        "USER": unquote(parsed.username) if parsed.username else "",
        "PASSWORD": unquote(parsed.password) if parsed.password else "",
        "HOST": host,
        "PORT": str(parsed.port) if parsed.port else "",
        "CONN_MAX_AGE": 0,
    }
    if not host or host.startswith("/"):
        config["HOST"] = host or "/var/run/postgresql"
    return config


def _database_config() -> dict:
    url = (os.environ.get("OPENATSC3_CA_DATABASE_URL")
           or os.environ.get("DATABASE_URL"))
    if not url:
        from ._embedded import embedded_uri
        url = embedded_uri()
    config = _parse_database_url(url)
    config["CONN_MAX_AGE"] = 600
    return config


DATABASES = {"default": _database_config()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

#: Root of the materialized PEM tree (root/, issuing/, broadcasters/<name>/).
OPENATSC3_CA_BASE_DIR = os.environ.get(
    "OPENATSC3_CA_BASE_DIR", str(BASE_DIR / "ca-tree"))

#: When the root key is held offline (air-gapped quorum), online issuance that
#: needs the root fails fast and points at the ceremony instead of touching a
#: local root key.  See the operator runbook (docs/ca-operator-runbook.rst).
OPENATSC3_CA_ROOT_OFFLINE = _flag("OPENATSC3_CA_ROOT_OFFLINE", False)

#: Where fulfilled bundles are delivered when no S3 bucket is configured.
OPENATSC3_CA_DELIVERY_DIR = os.environ.get(
    "OPENATSC3_CA_DELIVERY_DIR", str(BASE_DIR / "deliveries"))
#: S3 artifact bucket for signed-URL delivery; empty selects the local deliverer.
OPENATSC3_CA_S3_BUCKET = os.environ.get("OPENATSC3_CA_S3_BUCKET", "")
OPENATSC3_CA_S3_REGION = os.environ.get("OPENATSC3_CA_S3_REGION", "")
#: Seconds a delivered bundle's signed URL stays valid.
OPENATSC3_CA_DELIVERY_TTL = int(os.environ.get("OPENATSC3_CA_DELIVERY_TTL", "3600"))

#: Stripe Checkout (Phase 2 sales).  Empty key means checkout is unavailable.
OPENATSC3_CA_STRIPE_SECRET_KEY = os.environ.get(
    "OPENATSC3_CA_STRIPE_SECRET_KEY", "")
OPENATSC3_CA_STRIPE_WEBHOOK_SECRET = os.environ.get(
    "OPENATSC3_CA_STRIPE_WEBHOOK_SECRET", "")
#: Absolute base URL used for Stripe Checkout success/cancel redirects.
OPENATSC3_CA_PUBLIC_BASE_URL = os.environ.get(
    "OPENATSC3_CA_PUBLIC_BASE_URL", "http://localhost:8000")

#: Customer API: per-tenant request budget per minute (0 disables the limit).
OPENATSC3_CA_API_RATE_PER_MINUTE = int(
    os.environ.get("OPENATSC3_CA_API_RATE_PER_MINUTE", "60"))
#: Customer API: maximum request body size in bytes.
OPENATSC3_CA_API_MAX_BYTES = int(
    os.environ.get("OPENATSC3_CA_API_MAX_BYTES", str(1 << 20)))

#: Cache backend for the API rate limiter (locmem by default).
CACHES = {
    "default": {
        "BACKEND": os.environ.get(
            "OPENATSC3_CA_CACHE_BACKEND",
            "django.core.cache.backends.locmem.LocMemCache"),
        "LOCATION": os.environ.get("OPENATSC3_CA_CACHE_LOCATION", "openatsc3-ca"),
    },
}
