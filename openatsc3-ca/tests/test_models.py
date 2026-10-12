"""Model constraints and invariants (A/360 5.2.2.2, 5.3.1.6)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from openatsc3_ca.catalog.models import Certificate, RolloverPlan

pytestmark = pytest.mark.django_db


def test_signaling_signer_requires_bsid(ca):
    from openatsc3_ca.catalog import services

    cert = services.issue_broadcaster("WHUT", (540,))
    assert list(cert.bsids) == [540]
    assert cert.profile == "signaling-signer"
    assert cert.parent == ca["issuing"]


def test_root_has_no_parent(ca):
    assert ca["root"].parent is None
    assert ca["root"].profile == "root"


def test_tenant_ca_requires_a_tenant():
    bad = Certificate(
        serial="2", subject_dn="CN=t", profile="tenant-ca",
        not_before=timezone.now(),
        not_after=timezone.now() + dt.timedelta(days=1))
    with pytest.raises(ValidationError):
        bad.full_clean()


def test_cert_validity_ordered():
    bad = Certificate(
        serial="1", subject_dn="CN=x", profile="root",
        not_before=timezone.now(), not_after=timezone.now() - dt.timedelta(days=1))
    with pytest.raises(ValidationError):
        bad.full_clean()


def test_rollover_window_cannot_precede_next_from(ca):
    from openatsc3_ca.catalog import services

    current = services.issue_broadcaster("WHUT", (540,))
    nxt = services.issue_broadcaster("WHUT", (540,))
    now = timezone.now()
    plan = RolloverPlan(
        broadcaster=current.broadcaster, current=current, next=nxt,
        next_from=now, current_until=now - dt.timedelta(hours=1))
    with pytest.raises(ValidationError):
        plan.full_clean()


def test_rollover_window_equal_is_allowed(ca):
    from openatsc3_ca.catalog import services

    current = services.issue_broadcaster("WHUT", (540,))
    nxt = services.issue_broadcaster("WHUT", (540,))
    now = timezone.now()
    plan = RolloverPlan(
        broadcaster=current.broadcaster, current=current, next=nxt,
        next_from=now, current_until=now)  # equal: CurrentCertUntil >= NextCertFrom
    plan.full_clean()


def test_audit_event_is_append_only():
    from openatsc3_ca.catalog.models import AuditEvent

    event = AuditEvent.objects.create(actor="tester", action="test")
    event.action = "changed"
    with pytest.raises(ValidationError):
        event.save()
    with pytest.raises(ValidationError):
        event.delete()


def test_broadcaster_names_are_scoped_per_tenant(ca):
    from django.db import IntegrityError, transaction

    from openatsc3_ca.catalog.models import Broadcaster, Tenant

    acme = Tenant.objects.create(name="Acme", slug="acme")
    beta = Tenant.objects.create(name="Beta", slug="beta")
    Broadcaster.objects.create(name="SHARED", tenant=acme)
    Broadcaster.objects.create(name="SHARED", tenant=beta)  # allowed: other tenant
    assert Broadcaster.objects.filter(name="SHARED").count() == 2

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Broadcaster.objects.create(name="SHARED", tenant=acme)

    # The legacy (tenant=None) scope keeps its own name uniqueness.
    Broadcaster.objects.create(name="LEGACY")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Broadcaster.objects.create(name="LEGACY")


def test_issue_broadcaster_namespaces_by_tenant(ca):
    from openatsc3_ca.catalog import services
    from openatsc3_ca.catalog.models import Broadcaster, Tenant

    acme = Tenant.objects.create(name="Acme", slug="acme")
    beta = Tenant.objects.create(name="Beta", slug="beta")
    for tenant in (acme, beta):
        services.issue_tenant_ca(tenant)
        services.issue_broadcaster("KXYZ", (551,), tenant=tenant)

    assert Broadcaster.objects.filter(name="KXYZ").count() == 2
    assert Broadcaster.objects.get(name="KXYZ", tenant=acme).tenant_id == acme.id
    assert Broadcaster.objects.get(name="KXYZ", tenant=beta).tenant_id == beta.id
