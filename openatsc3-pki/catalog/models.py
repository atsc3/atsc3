"""The certificate-lifecycle ledger (PostgreSQL is the source of truth).

These models hold **no cryptography**: they record what ``openatsc3_pki``
issued and what the operator decided.  The DER/PEM/key bytes live on disk (keys
are 0600) and are referenced by path + SHA-256; ``export`` materializes the PEM
tree the receiver/signer read.

Spec sources are named per field.  Constraints that the standard states
positively (A/360 5.2.2.2, 5.2.2.6) are enforced in ``clean()`` *and* as
database constraints where PostgreSQL can express them.
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .constants import CertStatus, Profile, RolloverState


class Broadcaster(models.Model):
    """A broadcast organization that owns signaling-signer certificates."""

    name = models.CharField(max_length=128, unique=True)
    organization = models.CharField(max_length=255, blank=True)
    #: The registered bsid set for this organization (A/360 id-atsc-sdattr-bsid).
    bsids = ArrayField(models.IntegerField(), default=list, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Certificate(models.Model):
    """An issued X.509 certificate and its operational status."""

    #: Decimal serial text — an X.509 serial is up to 159 bits, beyond BigInteger.
    serial = models.CharField(max_length=64, unique=True)
    subject_dn = models.CharField(max_length=512)
    common_name = models.CharField(max_length=255, blank=True)
    organization = models.CharField(max_length=255, blank=True)
    organizational_unit = models.CharField(max_length=255, blank=True)

    profile = models.CharField(max_length=32, choices=Profile.CHOICES)
    not_before = models.DateTimeField()
    not_after = models.DateTimeField()

    #: DER file on disk (the certificate itself).
    der_file = models.CharField(max_length=1024)
    #: SHA-1 SubjectKeyIdentifier, hex (A/360 5.2.2.2), indexed for chain lookup.
    ski = models.CharField(max_length=64, db_index=True)
    #: AuthorityKeyIdentifier, hex, when present.
    aki = models.CharField(max_length=64, blank=True)
    #: id-atsc-sdattr-bsid values (A/360 5.3.1.6).
    bsids = ArrayField(models.IntegerField(), default=list, blank=True)
    #: ExtendedKeyUsage OIDs, as dotted strings (A/360 5.3.1.6).
    eku = ArrayField(models.CharField(max_length=64), default=list, blank=True)

    #: Issuing certificate (self for the root).
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="children")

    #: Private key file (0600) and its SHA-256, for on-disk integrity checks.
    key_path = models.CharField(max_length=1024, blank=True)
    key_sha256 = models.CharField(max_length=64, blank=True)

    status = models.CharField(
        max_length=16, choices=CertStatus.CHOICES, default=CertStatus.GOOD,
        db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=128, blank=True)

    #: The broadcaster this signer belongs to (signaling signers only).
    broadcaster = models.ForeignKey(
        Broadcaster, null=True, blank=True, on_delete=models.PROTECT,
        related_name="certificates")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(not_after__gt=models.F("not_before")),
                name="cert_validity_ordered"),
            models.CheckConstraint(
                condition=models.Q(status__in=[c[0] for c in CertStatus.CHOICES]),
                name="cert_status_known"),
        ]

    def __str__(self) -> str:
        return f"{self.common_name or self.subject_dn} [{self.profile}]"

    def clean(self) -> None:
        errors = {}
        if self.not_after and self.not_before and self.not_after <= self.not_before:
            errors["not_after"] = "notAfter must be after notBefore"
        if self.profile == Profile.SIGNALING_SIGNER and not self.bsids:
            # A/360 5.3.1.6: a signaling signer must list at least one bsid.
            errors["bsids"] = "a signaling signer must list at least one bsid"
        if self.profile == Profile.ROOT and self.parent_id is not None:
            errors["parent"] = "the root certificate has no parent"
        if errors:
            raise ValidationError(errors)

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.not_after

    def effective_status(self) -> str:
        """The status a verifier would derive: revoked wins, then expiry."""
        if self.status == CertStatus.REVOKED:
            return CertStatus.REVOKED
        if self.is_expired:
            return CertStatus.EXPIRED
        return self.status


class OcspResponder(models.Model):
    """The OCSP responder certificate/key currently in use (A/360 5.3.1.7)."""

    common_name = models.CharField(max_length=255)
    certificate = models.ForeignKey(
        Certificate, on_delete=models.PROTECT, related_name="as_responder")
    key_path = models.CharField(max_length=1024)
    key_sha256 = models.CharField(max_length=64, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.common_name


class OcspResponse(models.Model):
    """A stapled OCSP response — the persisted revocation record (A/360 5.2.2.2).

    One row per generated response; the latest by ``produced_at`` for a
    certificate is the current status the receiver would staple.
    """

    certificate = models.ForeignKey(
        Certificate, on_delete=models.CASCADE, related_name="ocsp_responses")
    issuer = models.ForeignKey(
        Certificate, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(
        max_length=16, choices=CertStatus.CHOICES, default=CertStatus.GOOD)
    produced_at = models.DateTimeField()
    this_update = models.DateTimeField(null=True, blank=True)
    next_update = models.DateTimeField(null=True, blank=True)
    der_file = models.CharField(max_length=1024)
    sha256 = models.CharField(max_length=64)
    responder = models.ForeignKey(
        OcspResponder, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="responses")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-produced_at"]
        indexes = [models.Index(fields=["certificate", "-produced_at"])]

    def __str__(self) -> str:
        return f"OCSP {self.certificate_id} {self.status} @ {self.produced_at}"


class RolloverPlan(models.Model):
    """A key-replacement window (A/360 5.2.2.2 ``CertReplacement``).

    ``CurrentCertUntil`` may be later than, but **not earlier than**,
    ``NextCertFrom`` (A/360 CDT element description).  Both the model and a
    database constraint enforce it.
    """

    broadcaster = models.ForeignKey(
        Broadcaster, on_delete=models.CASCADE, related_name="rollovers")
    current = models.ForeignKey(
        Certificate, on_delete=models.PROTECT, related_name="as_current")
    next = models.ForeignKey(
        Certificate, on_delete=models.PROTECT, related_name="as_next")
    next_from = models.DateTimeField()
    current_until = models.DateTimeField()
    state = models.CharField(
        max_length=16, choices=RolloverState.CHOICES,
        default=RolloverState.PLANNED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(current_until__gte=models.F("next_from")),
                name="rollover_window_ordered"),
        ]

    def __str__(self) -> str:
        return f"rollover {self.broadcaster} {self.state}"

    def clean(self) -> None:
        errors = {}
        if (self.next_from and self.current_until
                and self.current_until < self.next_from):
            errors["current_until"] = (
                "CurrentCertUntil cannot be earlier than NextCertFrom "
                "(A/360 5.2.2.2)")
        if self.current_id and self.next_id:
            if self.current_id == self.next_id:
                errors["next"] = "NextCert must differ from CurrentCert"
            if (self.current.profile != Profile.SIGNALING_SIGNER
                    or self.next.profile != Profile.SIGNALING_SIGNER):
                errors["next"] = "both certificates must be signaling signers"
            if (self.current.broadcaster_id and self.next.broadcaster_id
                    and self.current.broadcaster_id != self.next.broadcaster_id):
                errors["next"] = "both certificates must share a broadcaster"
        if errors:
            raise ValidationError(errors)


class PublishedSignaling(models.Model):
    """A snapshot of signed signaling assembled from the ledger (audit/regenerate).

    Phase 1 records the snapshot; the ``publish`` command that rebuilds it from
    live status is Phase 2.
    """

    broadcaster = models.ForeignKey(
        Broadcaster, on_delete=models.CASCADE, related_name="published")
    signing_time = models.DateTimeField()
    signer = models.ForeignKey(
        Certificate, on_delete=models.PROTECT, related_name="+")
    cdt_file = models.CharField(max_length=1024, blank=True)
    smt_file = models.CharField(max_length=1024, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"published {self.broadcaster} @ {self.signing_time}"


class SpecReference(models.Model):
    """Seed/reference data derived from the spec (migration target).

    Backfilled by the reference-data data migration so the operator's OIDs,
    profile constants and trust anchors survive model evolution.
    """

    key = models.CharField(max_length=128, unique=True)
    value = models.CharField(max_length=512)
    ref = models.CharField(max_length=128, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["key"]

    def __str__(self) -> str:
        return self.key


class AuditEvent(models.Model):
    """Append-only audit trail.  Rows may never be updated or deleted."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.CharField(max_length=150, default="system")
    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("AuditEvent rows are append-only")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("AuditEvent rows are append-only")
