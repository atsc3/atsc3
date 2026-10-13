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
