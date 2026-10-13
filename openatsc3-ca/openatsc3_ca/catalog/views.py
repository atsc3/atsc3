"""The Stripe webhook endpoint (the only non-admin HTTP surface in v1).

Stripe posts ``checkout.session.completed`` here; the order is moved to
``paid`` and awaits manual approval in the admin.  The endpoint is CSRF-exempt
because the caller is Stripe, authenticated by the webhook signature.
"""

from __future__ import annotations

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import services


@require_GET
def healthz(request):
    """Liveness/readiness probe for the load balancer (a DB round-trip)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return HttpResponse("database unavailable", status=503)
    return HttpResponse("ok")


@csrf_exempt
@require_POST
def stripe_webhook(request):
    if not getattr(settings, "OPENATSC3_CA_STRIPE_SECRET_KEY", ""):
        return HttpResponseBadRequest("stripe is not configured")
    signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    try:
        order = services.handle_webhook(request.body, signature)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    return HttpResponse("ok" if order is None else f"order {order.pk}")
