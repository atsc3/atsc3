"""Customer-facing JSON API: read-only status + LLS-table (SMT) signing.

Deliberately plain Django (no DRF): v1 stays dependency-light and the webhook is
the only other non-admin surface.  A tenant authenticates with its ``api_token``
via ``Authorization: Token <t>`` (or ``X-API-Token``); tokens may expire
(``Tenant.api_token_expires_at``).

* ``GET  /api/v1/tenant/``        — the tenant and its broadcasters.
* ``GET  /api/v1/certificates/``  — the tenant's certificates (read-only).
* ``POST /api/v1/smt/``           — sign uploaded LLS tables, return CDT + SMT.

The SMT endpoint is the product's "we sign your tables" boundary: the customer
uploads the LLS table bodies, and the service returns the signed ``0x07``
SignedMultiTable plus its CertificationData ``0x06`` (A/331 6.7, A/360 5.2.2.2).
Requests are size-capped and rate-limited per tenant; every signing call is
audited.
"""

from __future__ import annotations

import base64
import binascii
import json

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from openatsc3_pki import cdt as pki_cdt

from . import services
from .models import Broadcaster, Certificate, Tenant


def _tenant_from_request(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    token = header[len("Token "):].strip() if header.lower().startswith("token ") else ""
    token = token or request.META.get("HTTP_X_API_TOKEN", "").strip()
    if not token:
        return None
    return Tenant.objects.filter(api_token=token, active=True).first()


def _rate_limit_exceeded(tenant: Tenant) -> bool:
    """Fixed-window per-tenant request budget (``..._API_RATE_PER_MINUTE``)."""
    limit = getattr(settings, "OPENATSC3_CA_API_RATE_PER_MINUTE", 60)
    if limit <= 0:
        return False
    key = f"openatsc3ca:api-rate:{tenant.pk}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.add(key, 1, timeout=60)
        count = 1
    return count > limit


def _authorize(request):
    """Return ``(tenant, None)`` or ``(None, error_response)``."""
    tenant = _tenant_from_request(request)
    if tenant is None:
        return None, JsonResponse({"error": "missing or invalid API token"},
                                  status=401)
    if (tenant.api_token_expires_at is not None
            and tenant.api_token_expires_at <= timezone.now()):
        return None, JsonResponse({"error": "API token expired"}, status=401)
    if _rate_limit_exceeded(tenant):
        return None, JsonResponse({"error": "rate limit exceeded"}, status=429)
    return tenant, None


def _too_large(request) -> bool:
    limit = getattr(settings, "OPENATSC3_CA_API_MAX_BYTES", 1 << 20)
    length = request.META.get("CONTENT_LENGTH") or 0
    try:
        return int(length) > limit
    except (TypeError, ValueError):
        return False


@require_GET
def tenant_info(request):
    """The authenticated tenant and its broadcaster names."""
    tenant, error = _authorize(request)
    if error is not None:
        return error
    broadcasters = sorted(
        Broadcaster.objects.filter(tenant=tenant).values_list("name", flat=True))
    return JsonResponse({
        "slug": tenant.slug,
        "name": tenant.name,
        "organization": tenant.organization,
        "custody": tenant.custody,
        "broadcasters": broadcasters,
    })


@require_GET
def certificates(request):
    """The authenticated tenant's certificates (read-only)."""
    tenant, error = _authorize(request)
    if error is not None:
        return error
    rows = (Certificate.objects.filter(tenant=tenant)
            .select_related("broadcaster").order_by("-created_at"))
    return JsonResponse({"certificates": [_cert_json(c) for c in rows]})


def _cert_json(cert: Certificate) -> dict:
    return {
        "serial": cert.serial,
        "common_name": cert.common_name,
        "profile": cert.profile,
        "custody": cert.custody,
        "status": cert.status,
        "not_after": cert.not_after.isoformat(),
        "broadcaster": cert.broadcaster.name if cert.broadcaster_id else None,
    }


@csrf_exempt
@require_POST
def upload_smt(request):
    """Sign the customer's LLS tables and return the CDT ``0x06`` + SMT ``0x07``."""
    tenant, error = _authorize(request)
    if error is not None:
        return error
    if _too_large(request):
        return JsonResponse({"error": "request body too large"}, status=413)
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"invalid JSON: {exc}"}, status=400)

    broadcaster = Broadcaster.objects.filter(
        tenant=tenant, name=(body.get("broadcaster") or "").strip()).first()
    if broadcaster is None:
        return JsonResponse(
            {"error": f"no broadcaster {body.get('broadcaster')!r} for tenant"},
            status=404)
    try:
        tables = [_lls_table(entry) for entry in body.get("tables", [])]
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        return JsonResponse({"error": f"bad table entry: {exc}"}, status=400)
    if not tables:
        return JsonResponse({"error": "no tables supplied"}, status=400)

    try:
        _published, cdt_table, smt = services.publish(
            broadcaster, tables=tables, actor=f"api:{tenant.slug}")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    services.audit("api-smt", actor=f"api:{tenant.slug}", target=broadcaster,
                   detail={"tables": len(tables)})
    return JsonResponse({
        "cdt_b64": base64.b64encode(cdt_table.gzip_bytes()).decode("ascii"),
        "smt_b64": base64.b64encode(smt).decode("ascii"),
    })


def _lls_table(entry: dict) -> pki_cdt.LlsTable:
    """A request-JSON LLS table: ``{table_id, version, payload_b64}``."""
    return pki_cdt.LlsTable(
        table_id=int(entry["table_id"]),
        version=int(entry["version"]),
        payload=base64.b64decode(entry["payload_b64"], validate=True))
