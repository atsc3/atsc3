"""Offline-root ceremony through the CA app: request -> sign offline -> import.

The root key stays on the air-gapped side; the online app only ever handles the
public root certificate and the signed result.
"""

from __future__ import annotations

import pytest

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.models import (
    Broadcaster,
    OcspResponder,
    Tenant,
)

pytestmark = pytest.mark.django_db


def test_offline_tenant_ca_round_trip_publishes(ca):
    from openatsc3_pki import ceremony, keys, verify

    tenant = Tenant.objects.create(name="Acme", slug="acme")
    request = services.request_tenant_ca(tenant)

    # Offline side holds the root key; the online side only had the request.
    signed = ceremony.sign_request(request, keys.load_private(ca["root"].key_path))
    tca = services.import_signed_certificate(signed.der(), "tenant-ca", tenant=tenant)

    assert tca.parent == ca["root"]
    assert services.tenant_ca(tenant).serial == tca.serial

    services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)
    services.issue_cdt_signer("ACME-TV", (540,), tenant=tenant)
    services.issue_ocsp_responder(tenant=tenant)
    broadcaster = Broadcaster.objects.get(name="ACME-TV", tenant=tenant)

    _published, table, _smt = services.publish(broadcaster)
    verdict, _ = verify.verify_certification_data(
        table, [services.certificate_x509(ca["root"])])
    assert verdict.ok, verdict.reason


def test_request_root_responder_round_trip(ca):
    from openatsc3_pki import ceremony, keys

    request = services.request_root_responder()
    signed = ceremony.sign_request(request, keys.load_private(ca["root"].key_path))
    cert = services.import_signed_certificate(
        signed.der(), "ocsp-responder", make_responder=True)

    assert cert.parent == ca["root"]
    assert OcspResponder.objects.filter(certificate=cert, active=True).exists()


def test_import_rejects_wrong_public_key(ca):
    from openatsc3_pki import ceremony, keys

    tenant = Tenant.objects.create(name="Acme", slug="acme")
    request = services.request_tenant_ca(tenant)
    signed = ceremony.sign_request(request, keys.load_private(ca["root"].key_path))
    keys.write_private(
        services._authority().tenant_key_path(tenant.slug),
        keys.generate(keys.CA_CURVE))
    with pytest.raises(ValueError, match="public key"):
        services.import_signed_certificate(signed.der(), "tenant-ca", tenant=tenant)


def test_import_rejects_wrong_parent(ca, tmp_path):
    from openatsc3_pki import ca as pki_ca, ceremony

    tenant = Tenant.objects.create(name="Acme", slug="acme")
    request = services.request_tenant_ca(tenant)
    other = pki_ca.CertificateAuthority(str(tmp_path / "other"))
    other.init_root(common_name="Other Root")
    with pytest.raises(ValueError, match="AuthorityKeyIdentifier"):
        ceremony.sign_request(request, other.load_root_key())


def test_import_rejects_non_ca_as_tenant_ca(ca):
    from openatsc3_pki import keys, x509
    from openatsc3_pki.x509 import SubjectInfo

    tenant = Tenant.objects.create(name="Acme", slug="acme")
    request = services.request_tenant_ca(tenant)
    leaf_key = keys.load_private(
        services._authority().tenant_key_path(tenant.slug))
    leaf = x509.issue_signaling_signer(
        SubjectInfo(common_name="Not a CA", organizational_unit="x",
                    organization="Acme"),
        leaf_key, services.certificate_x509(ca["root"]),
        keys.load_private(ca["root"].key_path), (540,))
    with pytest.raises(ValueError, match="must be a CA"):
        services.import_signed_certificate(leaf.der(), "tenant-ca", tenant=tenant)


def test_import_leaf_with_key_path(ca, tmp_path):
    from openatsc3_pki import ceremony, keys, x509
    from openatsc3_pki.x509 import SubjectInfo

    tenant = Tenant.objects.create(name="Acme", slug="acme")
    request = services.request_tenant_ca(tenant)
    signed = ceremony.sign_request(request, keys.load_private(ca["root"].key_path))
    tca = services.import_signed_certificate(signed.der(), "tenant-ca", tenant=tenant)
    broadcaster = Broadcaster.objects.create(
        name="ACME-TV", organization="Acme", bsids=[540], tenant=tenant)
    tenant_ca = services.certificate_x509(tca)
    tenant_key = services._authority().load_tenant_key(tenant.slug)
    leaf_key = keys.generate(keys.SIGNER_CURVE)
    leaf = x509.issue_signaling_signer(
        SubjectInfo(common_name="ACME-TV-LLS-Signer",
                    organizational_unit="ATSC Broadcast Signaling Signer",
                    organization="Acme"),
        leaf_key, tenant_ca, tenant_key, (540,))
    key_path = tmp_path / "leaf.key.pem"
    keys.write_private(str(key_path), leaf_key)
    imported = services.import_signed_certificate(
        leaf.der(), "signaling-signer", tenant=tenant,
        broadcaster=broadcaster, key_path=str(key_path))
    assert imported.parent == tca
    assert imported.key_path == str(key_path)


def test_serial_reuse_with_different_der_is_rejected(ca):
    root = ca["root"]
    with pytest.raises(ValueError, match="already used"):
        services._ensure_serial_available(root.serial, b"different certificate")
