"""Tenant key-custody tiers: standard wrapped leaves vs premium KMS keys."""

from __future__ import annotations

import pytest

pytest.importorskip("django")
pytest.importorskip("pytest_django")

from openatsc3_ca.catalog import custody, services
from openatsc3_ca.catalog.constants import KeyCustody
from openatsc3_ca.catalog.models import Broadcaster, Tenant
from fake_kms import FakeKms

pytestmark = pytest.mark.django_db


@pytest.fixture
def aws_kms(settings):
    client = FakeKms()
    settings.OPENATSC3_CA_KEY_BACKEND = "aws"
    settings.OPENATSC3_CA_KMS_CLIENT = client
    return client


def _tenant(slug="acme", custody_tier=KeyCustody.STANDARD):
    return Tenant.objects.create(name=slug.title(), slug=slug,
                                 custody=custody_tier)


def _tenant_ca(tenant):
    services.issue_tenant_ca(tenant)


def test_standard_leaf_is_wrapped_and_signs(ca, aws_kms):
    tenant = _tenant()
    _tenant_ca(tenant)
    cert = services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)

    assert cert.custody == KeyCustody.STANDARD
    assert cert.wrapped_key
    assert cert.wrapped_public_key
    assert cert.wrapping_key_id == tenant.wrapping_key_id
    assert cert.kms_key_id == ""
    signer = services._cert_signer(cert)
    from openatsc3_pki import cms
    from openatsc3_ca.catalog import services as svc

    blob = cms.sign_detached(b"payload", svc.certificate_x509(cert), signer)
    assert cms.verify(blob, b"payload", svc.certificate_x509(cert).cert.public_key())


def test_premium_leaf_is_a_kms_key(ca, aws_kms):
    tenant = _tenant(custody_tier=KeyCustody.PREMIUM)
    _tenant_ca(tenant)
    cert = services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)

    assert cert.custody == KeyCustody.PREMIUM
    assert cert.kms_key_id
    assert cert.wrapped_key == ""
    assert cert.wrapping_key_id == ""
    from openatsc3_pki import cms
    signer = services._cert_signer(cert)
    blob = cms.sign_detached(b"payload", services.certificate_x509(cert), signer)
    assert cms.verify(blob, b"payload", services.certificate_x509(cert).cert.public_key())


def test_standard_publish_round_trip(ca, aws_kms):
    from openatsc3_pki import verify

    tenant = _tenant()
    _tenant_ca(tenant)
    services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)
    services.issue_cdt_signer("ACME-TV", (540,), tenant=tenant)
    services.issue_ocsp_responder(tenant=tenant)
    broadcaster = Broadcaster.objects.get(name="ACME-TV", tenant=tenant)
    _published, table, _smt = services.publish(broadcaster)
    verdict, _ = verify.verify_certification_data(
        table, [services.certificate_x509(ca["root"])])
    assert verdict.ok, verdict.reason


def test_tenant_wrapping_key_is_created_once(ca, aws_kms):
    tenant = _tenant()
    first = custody.ensure_tenant_wrapping_key(tenant)
    second = custody.ensure_tenant_wrapping_key(tenant)
    assert first == second
    assert len(aws_kms.keys) == 1


def test_standard_falls_back_to_local_when_aws_is_disabled(ca, settings):
    settings.OPENATSC3_CA_KEY_BACKEND = "local"
    tenant = _tenant()
    _tenant_ca(tenant)
    cert = services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)
    assert cert.custody == KeyCustody.LOCAL
    assert cert.key_path


def test_fulfill_order_uses_order_custody(ca, aws_kms, tmp_path):
    from openatsc3_ca.catalog.delivery import LocalDeliverer
    from openatsc3_ca.catalog.models import Order

    order = Order.objects.create(
        email="ops@example.com", organization="Acme",
        broadcaster_name="ACME-TV", bsids=[540], amount_cents=2500,
        custody=KeyCustody.STANDARD)
    services.mark_order_paid(order)
    services.fulfill_order(order, deliverer=LocalDeliverer(tmp_path), actor="test")
    order.refresh_from_db()
    assert order.tenant.custody == KeyCustody.STANDARD
    signer = order.broadcaster.certificates.get(
        profile="signaling-signer", status="good")
    assert signer.custody == KeyCustody.STANDARD
    assert signer.wrapped_key
