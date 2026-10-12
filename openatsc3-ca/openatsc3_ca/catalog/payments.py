"""Payment-gateway seam: Stripe Checkout, injected so it is testable offline.

The CA app never imports ``stripe`` at module load; :class:`StripeGateway`
imports it lazily, mirroring the KMS signer seam.  Tests inject a fake gateway
that returns the same shapes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PaymentGateway(Protocol):
    """Creates Checkout Sessions and parses webhook events."""

    def create_checkout(self, *, amount_cents: int, currency: str,
                        description: str, metadata: dict, success_url: str,
                        cancel_url: str) -> dict:
        """Return ``{"id": <session id>, "url": <checkout url>}``."""
        ...

    def parse_webhook(self, payload: bytes, signature: str) -> dict:
        """Verify the signature and return the event as a plain mapping."""
        ...


class StripeGateway:
    """Stripe Checkout via the ``stripe`` SDK (``pip install -e '.[stripe]'``).

    ``client`` is an injected ``stripe`` module-like object; when omitted the
    real package is imported and configured with ``secret_key``.
    """

    def __init__(self, *, secret_key: str = "", webhook_secret: str = "",
                 client=None) -> None:
        if client is None:
            import stripe
            client = stripe
            client.api_key = secret_key
        self.client = client
        self.webhook_secret = webhook_secret

    def create_checkout(self, *, amount_cents: int, currency: str,
                        description: str, metadata: dict, success_url: str,
                        cancel_url: str) -> dict:
        session = self.client.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": currency,
                    "unit_amount": amount_cents,
                    "product_data": {"name": description},
                },
                "quantity": 1,
            }],
            metadata=metadata,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return {"id": session["id"], "url": session["url"]}

    def parse_webhook(self, payload: bytes, signature: str) -> dict:
        if self.webhook_secret:
            event = self.client.Webhook.construct_event(
                payload, signature, self.webhook_secret)
        else:
            import json
            event = json.loads(payload)
        return dict(event)


def default_gateway() -> PaymentGateway:
    """The configured Stripe gateway (empty secret key disables checkout)."""
    from django.conf import settings

    return StripeGateway(
        secret_key=getattr(settings, "OPENATSC3_CA_STRIPE_SECRET_KEY", ""),
        webhook_secret=getattr(settings, "OPENATSC3_CA_STRIPE_WEBHOOK_SECRET", ""))
