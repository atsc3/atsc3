"""Phase 2 sales + delivery: Orders, Stripe Checkout/webhook, S3 presign."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.delivery import LocalDeliverer, S3Deliverer
from openatsc3_ca.catalog.models import Order

pytestmark = pytest.mark.django_db


class FakeGateway:
    """A minimal Stripe stand-in: Checkout Session + JSON webhook."""

    def __init__(self):
        self.sessions = {}

    def create_checkout(self, *, amount_cents, currency, description, metadata,
                        success_url, cancel_url):
        session_id = f"cs_test_{len(self.sessions) + 1}"
        self.sessions[session_id] = metadata
        return {"id": session_id, "url": f"https://checkout.example/{session_id}"}

    def parse_webhook(self, payload, signature):
        return json.loads(payload)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return f"https://s3.example/{Params['Bucket']}/{Params['Key']}?ttl={ExpiresIn}"


def _order(**overrides):
    values = dict(email="buyer@example.com", organization="Acme",
                  broadcaster_name="ACME-TV", bsids=[540], amount_cents=5000)
    values.update(overrides)
    return Order.objects.create(**values)


def test_checkout_records_the_session(ca):
    gateway = FakeGateway()
    order = _order()
    services.create_checkout(order, gateway=gateway)

    assert order.stripe_session_id.startswith("cs_test_")
    assert order.checkout_url == f"https://checkout.example/{order.stripe_session_id}"
    assert order.status == "pending"


def test_webhook_marks_the_order_paid(ca):
    gateway = FakeGateway()
    order = _order()
    services.create_checkout(order, gateway=gateway)
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"id": order.stripe_session_id,
                            "payment_intent": "pi_1",
                            "metadata": {"order": str(order.pk)}}},
    }).encode()

    result = services.handle_webhook(payload, "sig", gateway=gateway)

    result.refresh_from_db()
    assert result.status == "paid"
    assert result.stripe_payment_intent == "pi_1"
    assert result.paid_at is not None


def test_webhook_ignores_other_events(ca):
    gateway = FakeGateway()
    payload = json.dumps({"type": "payment_intent.created", "data": {}}).encode()
    assert services.handle_webhook(payload, "sig", gateway=gateway) is None


def test_fulfill_issues_and_delivers_the_bundle(ca, tmp_path):
    order = _order()
    services.mark_order_paid(order)
    deliverer = LocalDeliverer(tmp_path / "deliveries")

    services.fulfill_order(order, deliverer=deliverer)

    order.refresh_from_db()
    assert order.status == "fulfilled"
    assert order.tenant is not None and order.broadcaster is not None
    assert order.fulfilled_at is not None
    out = Path(order.delivery_url)
    assert (out / "certs.pem").exists()
    assert (out / "cdt.xml.gz").exists()
    assert (out / "crl.pem").exists()
    assert list(out.glob("ocsp-*.der"))
    assert "-----BEGIN CERTIFICATE-----" in (out / "certs.pem").read_text()


def test_fulfill_requires_a_paid_order(ca):
    order = _order()
    with pytest.raises(ValueError):
        services.fulfill_order(order)


def test_fulfill_requires_bsids(ca, tmp_path):
    order = _order(bsids=[])
    services.mark_order_paid(order)
    with pytest.raises(ValueError):
        services.fulfill_order(order, deliverer=LocalDeliverer(tmp_path))


def test_s3_deliverer_uploads_and_presigns():
    client = FakeS3()
    deliverer = S3Deliverer("certs-bucket", client=client)
    url = deliverer.deliver("order-1", {"certs.pem": b"x", "cdt.xml.gz": b"y"},
                            expires_in=60)

    assert url.startswith("https://s3.example/certs-bucket/order-1/certs.pem")
    assert client.objects[("certs-bucket", "order-1/certs.pem")] == b"x"
    assert client.objects[("certs-bucket", "order-1/cdt.xml.gz")] == b"y"


def _fulfilled_order(tmp_path):
    order = _order()
    services.mark_order_paid(order)
    services.fulfill_order(order, deliverer=LocalDeliverer(tmp_path / "deliveries"))
    return order


def test_refund_revokes_the_tenant_ca(ca, tmp_path):
    order = _fulfilled_order(tmp_path)
    tenant_ca = services.tenant_ca(order.tenant)

    services.refund_order(order)

    order.refresh_from_db()
    tenant_ca.refresh_from_db()
    assert order.status == "refunded"
    assert order.refunded_at is not None
    assert tenant_ca.status == "revoked"


def test_refund_can_keep_the_tenant(ca, tmp_path):
    order = _fulfilled_order(tmp_path)
    services.refund_order(order, revoke_tenant=False)

    order.refresh_from_db()
    assert order.status == "refunded"
    assert services.tenant_ca(order.tenant).status == "good"


def test_refund_webhook_locates_the_order(ca, tmp_path):
    gateway = FakeGateway()
    order = _fulfilled_order(tmp_path)
    order.stripe_payment_intent = "pi_refund"
    order.save(update_fields=["stripe_payment_intent"])

    payload = json.dumps({
        "type": "charge.refunded",
        "data": {"object": {"object": "charge", "payment_intent": "pi_refund"}},
    }).encode()

    result = services.handle_webhook(payload, "sig", gateway=gateway)

    result.refresh_from_db()
    assert result.pk == order.pk
    assert result.status == "refunded"
    assert services.tenant_ca(order.tenant).status == "revoked"


def test_payment_failure_cancels_a_pending_order(ca):
    gateway = FakeGateway()
    order = _order()
    services.create_checkout(order, gateway=gateway)
    payload = json.dumps({
        "type": "payment_intent.payment_failed",
        "data": {"object": {"object": "payment_intent", "id": "pi_x",
                            "metadata": {"order": str(order.pk)}}},
    }).encode()

    result = services.handle_webhook(payload, "sig", gateway=gateway)

    result.refresh_from_db()
    assert result.status == "cancelled"
