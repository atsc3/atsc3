"""Phase 1 signed bundle: publish CDT (0x06) + signed SMT (0x07), and rollover."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from openatsc3_ca.catalog import services
from openatsc3_ca.catalog.models import (
    Broadcaster,
    PublishedSignaling,
    Tenant,
)

pytestmark = pytest.mark.django_db


def _tenant_bundle(name="Acme", slug="acme", bcast="ACME-TV", bsid=540):
    """A tenant with its CA, CurrentCert, CDT signer, and OCSP responder."""
    tenant = Tenant.objects.create(name=name, slug=slug)
    services.issue_tenant_ca(tenant)
    signer = services.issue_broadcaster(bcast, (bsid,), tenant=tenant)
    services.issue_cdt_signer(bcast, (bsid,), tenant=tenant)
    responder = services.issue_ocsp_responder(tenant=tenant)
    broadcaster = Broadcaster.objects.get(name=bcast)
    return tenant, broadcaster, signer, responder


def test_publish_builds_verifiable_cdt(ca):
    from openatsc3_pki import cdt, verify

    _tenant, broadcaster, _signer, _responder = _tenant_bundle()
    now = timezone.now().replace(microsecond=0)
    published, table, smt = services.publish(broadcaster, signing_time=now)

    assert smt is None
    assert published.cdt_file and not published.smt_file
    roots = [services.certificate_x509(ca["root"])]
    parsed = cdt.parse_certification_data(table.gzip_bytes())
    verdict, _ = verify.verify_certification_data(parsed, roots, now=now)
    assert verdict.ok, verdict.reason
    assert PublishedSignaling.objects.filter(broadcaster=broadcaster).count() == 1


def test_publish_signs_uploaded_lls_tables(ca):
    from openatsc3_pki import cdt, verify

    _tenant, broadcaster, _signer, _responder = _tenant_bundle()
    now = timezone.now().replace(microsecond=0)
    tables = [cdt.LlsTable(table_id=0x01, version=13, payload=b'<SLT bsid="540"/>')]
    published, table, smt = services.publish(
        broadcaster, tables=tables, signing_time=now)

    assert smt is not None and published.smt_file
    roots = [services.certificate_x509(ca["root"])]
    parsed = cdt.parse_certification_data(table.gzip_bytes())
    verdict, certified = verify.verify_certification_data(parsed, roots, now=now)
    assert verdict.ok, verdict.reason
    msg = cdt.parse_signed_multitable(smt)
    m = verify.verify_signed_message(msg.cms, msg.signed_extent, certified,
                                     [540], now=now)
    assert m.ok, m.reason


def test_rollover_publish_carries_next_cert(ca):
    from openatsc3_pki import cdt, verify

    tenant, broadcaster, signer, _responder = _tenant_bundle()
    next_signer = services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)
    now = timezone.now().replace(microsecond=0)
    services.start_rollover(signer, next_signer, now + dt.timedelta(hours=2),
                            now + dt.timedelta(hours=4))

    _published, table, _ = services.publish(broadcaster, signing_time=now)

    parsed = cdt.parse_certification_data(table.gzip_bytes())
    next_x509 = services.certificate_x509(next_signer)
    assert next_x509.der() in parsed.certificates
    roots = [services.certificate_x509(ca["root"])]
    verdict, certified = verify.verify_certification_data(parsed, roots, now=now)
    assert verdict.ok, verdict.reason
    assert certified.next_ski == next_x509.subject_key_identifier()


def test_complete_rollover_supersedes_current(ca):
    tenant, broadcaster, signer, _responder = _tenant_bundle()
    next_signer = services.issue_broadcaster("ACME-TV", (540,), tenant=tenant)
    now = timezone.now().replace(microsecond=0)
    plan = services.start_rollover(signer, next_signer, now,
                                   now + dt.timedelta(days=30))
    services.complete_rollover(plan)

    signer.refresh_from_db()
    plan.refresh_from_db()
    assert signer.status == "superseded"
    assert plan.state == "completed"
    assert services.current_signer(broadcaster).serial == next_signer.serial
