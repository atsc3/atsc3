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


def test_import_tenant_tree_and_responders(tmp_path, ca_base):
    from openatsc3_pki import ca as pki_ca, keys, x509 as pki_x509
    from openatsc3_pki.x509 import SubjectInfo

    from openatsc3_ca.catalog.models import OcspResponder, Tenant

    base = tmp_path / "tenant-ca"
    tree = pki_ca.CertificateAuthority(str(base))
    tree.init_root(common_name="Imported Root")
    tree.issue_tenant_ca("acme")
    tree.issue_broadcaster("ACME-TV", (541,), tenant="acme")

    # On-disk responders: a root-delegated one and a tenant one (the library
    # never writes responder certs, so the tree carries them explicitly).
    rkey = keys.generate()
    rcert = pki_x509.issue_ocsp_responder(
        SubjectInfo(common_name="Root OCSP",
                    organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        rkey, tree.load_root(), tree.load_root_key())
    rcert.to_pem_file(str(base / "root" / "ocsp-responder.cert.pem"))
    keys.write_private(str(base / "root" / "ocsp-responder.key.pem"), rkey)

    tkey = keys.generate()
    tcert = pki_x509.issue_ocsp_responder(
        SubjectInfo(common_name="Tenant OCSP",
                    organizational_unit="ATSC OCSP Responder",
                    organization="Acme"),
        tkey, tree.load_tenant("acme"), tree.load_tenant_key("acme"))
    tcert.to_pem_file(str(base / "tenants" / "acme" / "ocsp-responder.cert.pem"))
    keys.write_private(str(base / "tenants" / "acme" / "ocsp-responder.key.pem"), tkey)

    services.import_tree(base)

    assert Tenant.objects.filter(slug="acme").exists()
    assert Certificate.objects.filter(profile="tenant-ca", tenant__slug="acme").exists()
    assert Certificate.objects.filter(
        profile="signaling-signer", tenant__slug="acme", broadcaster__name="ACME-TV"
    ).exists()
    assert Broadcaster.objects.get(name="ACME-TV").tenant.slug == "acme"

    responders = OcspResponder.objects.filter(active=True)
    assert responders.filter(certificate__parent__profile="root").count() == 1
    assert responders.filter(certificate__tenant__slug="acme").count() == 1
