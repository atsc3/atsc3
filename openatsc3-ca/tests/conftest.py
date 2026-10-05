"""Fixtures for the Django/PostgreSQL CA tests.

The whole directory is skipped unless Django and pytest-django are installed, so
the plain crypto suite (``make test-pki``) runs without the web extra.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")
pytest.importorskip("pytest_django")


@pytest.fixture
def ca_base(tmp_path, settings):
    """Point the materialized PEM tree at a throwaway directory."""
    settings.OPENATSC3_CA_BASE_DIR = str(tmp_path / "ca-tree")
    return settings.OPENATSC3_CA_BASE_DIR


@pytest.fixture
def ca(ca_base):
    """A ready ledger: root + issuing CA + active OCSP responder."""
    from openatsc3_ca.catalog import services

    root = services.issue_root()
    issuing = services.issue_issuing()
    responder = services.issue_ocsp_responder()
    return {"root": root, "issuing": issuing, "responder": responder}
