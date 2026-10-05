"""Rootless embedded PostgreSQL for tests and local dev (no Docker).

``pgserver`` ships a relocatable PostgreSQL that runs entirely as the current
user on a Unix socket.  We keep one server handle alive for the process so the
cluster outlives individual connections; the first call initialises the data
directory, later calls reuse it.  This is the *test/dev* backend — the Docker
Compose service or a native PostgreSQL is the deployment backend, and both are
selected through the same ``resolve_database()`` settings helper.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_SERVER = None


def pgdata_dir() -> Path:
    """The embedded cluster's data directory (override: OPENATSC3_CA_PGDATA)."""
    env = os.environ.get("OPENATSC3_CA_PGDATA")
    if env:
        return Path(env)
    base = os.environ.get("OPENATSC3_CA_DATA_HOME")
    if base:
        return Path(base) / "pgdata"
    return Path(__file__).resolve().parents[1] / ".pgdata"


def embedded_uri(database: Optional[str] = None) -> str:
    """Start (or reuse) the embedded server and return its connection URI."""
    global _SERVER
    try:
        import pgserver
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pgserver is not installed; install the test extra "
            "(pip install -e '.[test]') or set OPENATSC3_CA_DATABASE_URL"
        ) from exc
    if _SERVER is None:
        data = pgdata_dir()
        data.mkdir(parents=True, exist_ok=True)
        _SERVER = pgserver.get_server(str(data), cleanup_mode=None)
    return _SERVER.get_uri(database)
