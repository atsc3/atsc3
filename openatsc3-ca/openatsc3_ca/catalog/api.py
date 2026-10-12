"""Customer-facing JSON API: read-only status + LLS-table (SMT) signing.

Deliberately plain Django (no DRF): v1 stays dependency-light and the webhook is
the only other non-admin surface.  A tenant authenticates with its ``api_token``
via ``Authorization: Token <t>`` (or ``X-API-Token``).

* ``GET  /api/v1/tenant/``        — the tenant and its broadcasters.
* ``GET  /api/v1/certificates/``  — the tenant's certificates (read-only).
* ``POST /api/v1/smt/``           — sign uploaded LLS tables, return CDT + SMT.

The SMT endpoint is the product's "we sign your tables" boundary: the customer
uploads the LLS table bodies, and the service returns the signed ``0x07``
SignedMultiTable plus its CertificationData ``0x06`` (A/331 6.7, A/360 5.2.2.2).
"""

from __future__ import annotations

import base64
import binascii
import json

from django.http import JsonResponse
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


def _unauthorized():
    return JsonResponse({"error": "missing or invalid API token"}, status=401)


@require_GET
def tenant_info(request):
    """The authenticated tenant and its broadcaster names."""
    tenant = _tenant_from_request(request)
    if tenant is None:
        return _unauthorized()
    broadcasters = sorted(
        Broadcaster.objects.filter(tenant=tenant).values_list("name", flat=True))
    return JsonResponse({
        "slug": tenant.slug,
        "name": tenant.name,
        "organization": tenant.organization,
        "broadcasters": broadcasters,
    })


@require_GET
def certificates(request):
    """The authenticated tenant's certificates (read-only)."""
    tenant = _tenant_from_request(request)
    if tenant is None:
        return _unauthorized()
    rows = (Certificate.objects.filter(tenant=tenant)
            .select_related("broadcaster").order_by("-created_at"))
    return JsonResponse({"certificates": [_cert_json(c) for c in rows]})


def _cert_json(cert: Certificate) -> dict:
    return {
        "serial": cert.serial,
        "common_name": cert.common_name,
        "profile": cert.profile,
        "status": cert.status,
        "not_after": cert.not_after.isoformat(),
        "broadcaster": cert.broadcaster.name if cert.broadcaster_id else None,
    }


@csrf_exempt
@require_POST
def upload_smt(request):
    """Sign the customer's LLS tables and return the CDT ``0x06`` + SMT ``0x07``."""
    tenant = _tenant_from_request(request)
    if tenant is None:
        return _unauthorized()
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
