"""Customer API: token auth, read-only status, and the SMT signing endpoint."""

from __future__ import annotations

import base64
import json

import pytest
from django.test import Client

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.models import Certificate, Tenant

pytestmark = pytest.mark.django_db


def _tenant_with_bundle(token="tok-acme"):
    tenant = Tenant.objects.create(name="Acme", slug="acme", api_token=token)
    services.issue_tenant_ca(tenant)
    services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)
    services.issue_cdt_signer("ACME-TV", (540,), tenant=tenant)
    services.issue_ocsp_responder(tenant=tenant)
    return tenant


def _auth(token):
    return {"HTTP_AUTHORIZATION": f"Token {token}"}


def test_healthz_ok():
    response = Client().get("/healthz")
    assert response.status_code == 200
    assert response.content == b"ok"


def test_api_requires_a_token(ca):
    _tenant_with_bundle()
    client = Client()
    assert client.get("/api/v1/tenant/").status_code == 401
    assert client.get("/api/v1/certificates/", **_auth("wrong")).status_code == 401


def test_api_lists_tenant_and_certificates(ca):
    _tenant_with_bundle()
    client = Client()

    info = client.get("/api/v1/tenant/", **_auth("tok-acme")).json()
    assert info["slug"] == "acme"
    assert "ACME-TV" in info["broadcasters"]

    certs = client.get("/api/v1/certificates/", **_auth("tok-acme")).json()
    profiles = {c["profile"] for c in certs["certificates"]}
    assert {"tenant-ca", "signaling-signer", "cdt-signer"} <= profiles


def test_smt_upload_returns_verifiable_bundle(ca):
    from openatsc3_pki import cdt, verify

    _tenant_with_bundle()
    client = Client()
    body = {
        "broadcaster": "ACME-TV",
        "tables": [{"table_id": 0x01, "version": 13,
                    "payload_b64": base64.b64encode(b'<SLT bsid="540"/>').decode()}],
    }
    resp = client.post("/api/v1/smt/", data=json.dumps(body),
                       content_type="application/json", **_auth("tok-acme"))
    assert resp.status_code == 200, resp.content
    payload = resp.json()

    root = Certificate.objects.get(profile="root")
    cdt_table = cdt.parse_certification_data(base64.b64decode(payload["cdt_b64"]))
    verdict, certified = verify.verify_certification_data(
        cdt_table, [services.certificate_x509(root)])
    assert verdict.ok, verdict.reason

    msg = cdt.parse_signed_multitable(base64.b64decode(payload["smt_b64"]))
    m = verify.verify_signed_message(msg.cms, msg.signed_extent, certified, [540])
    assert m.ok, m.reason


def test_smt_upload_unknown_broadcaster(ca):
    _tenant_with_bundle()
    client = Client()
    body = {"broadcaster": "NOPE",
            "tables": [{"table_id": 1, "version": 1, "payload_b64": "AAAA"}]}
    resp = client.post("/api/v1/smt/", data=json.dumps(body),
                       content_type="application/json", **_auth("tok-acme"))
    assert resp.status_code == 404


def test_api_token_expiry(ca):
    import datetime as _dt

    from django.utils import timezone

    tenant = _tenant_with_bundle()
    tenant.api_token_expires_at = timezone.now() - _dt.timedelta(seconds=1)
    tenant.save(update_fields=["api_token_expires_at"])

    client = Client()
    assert client.get("/api/v1/tenant/", **_auth("tok-acme")).status_code == 401


def test_api_rate_limit(ca, settings):
    from django.core.cache import cache

    cache.clear()
    settings.OPENATSC3_CA_API_RATE_PER_MINUTE = 2
    _tenant_with_bundle()
    client = Client()
    assert client.get("/api/v1/tenant/", **_auth("tok-acme")).status_code == 200
    assert client.get("/api/v1/tenant/", **_auth("tok-acme")).status_code == 200
    assert client.get("/api/v1/tenant/", **_auth("tok-acme")).status_code == 429


def test_smt_upload_size_cap(ca, settings):
    settings.OPENATSC3_CA_API_MAX_BYTES = 8
    _tenant_with_bundle()
    client = Client()
    resp = client.post("/api/v1/smt/", data=json.dumps({"broadcaster": "ACME-TV"}),
                       content_type="application/json", **_auth("tok-acme"))
    assert resp.status_code == 413


def test_smt_upload_is_audited(ca):
    from openatsc3_ca.catalog.models import AuditEvent

    _tenant_with_bundle()
    client = Client()
    body = {"broadcaster": "ACME-TV",
            "tables": [{"table_id": 1, "version": 13,
                        "payload_b64": base64.b64encode(b"x").decode()}]}
    client.post("/api/v1/smt/", data=json.dumps(body),
                content_type="application/json", **_auth("tok-acme"))
    assert AuditEvent.objects.filter(action="api-smt",
                                     actor="api:acme").exists()
