"""Importing a legacy on-disk tree into the ledger (the data-migration logic)."""

from __future__ import annotations

import pytest

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.models import Broadcaster, Certificate

pytestmark = pytest.mark.django_db


def _make_tree(base):
    from openatsc3_pki import ca as pki_ca

    tree = pki_ca.CertificateAuthority(str(base))
    tree.init_root(common_name="Imported Root")
    tree.issue_issuing(common_name="Imported Issuing CA")
    tree.issue_broadcaster("WHUT", (540,))
    return tree


def test_import_tree_populates_ledger(tmp_path):
    base = tmp_path / "legacy-ca"
    _make_tree(base)

    imported = services.import_tree(base)
    profiles = sorted(c.profile for c in imported)
    assert profiles == ["issuing-ca", "root", "signaling-signer"]

    root = Certificate.objects.get(profile="root")
    issuing = Certificate.objects.get(profile="issuing-ca")
    signer = Certificate.objects.get(profile="signaling-signer")
    assert signer.bsids == [540]
    assert signer.parent == issuing
    assert issuing.parent == root
    assert Broadcaster.objects.get(name="WHUT").bsids == [540]


def test_import_tree_is_idempotent(tmp_path):
    base = tmp_path / "legacy-ca"
    _make_tree(base)

    services.import_tree(base)
    services.import_tree(base)
    assert Certificate.objects.count() == 3
    assert Broadcaster.objects.count() == 1
