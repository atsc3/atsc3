"""Shared constants for the CA catalog.

Every value here is either a spec constant (A/360) or an operational bound, and
is defined once so no magic literals leak into the models or services.
"""

from __future__ import annotations

import datetime as _dt

#: A/331 6.7: table ids carried inside a SignedMultiTable.
LLS_CDT_TABLE_ID = 0x06
LLS_SIGNED_MULTITABLE_ID = 0x07

#: A/360 5.2.2.2: OCSPRefresh shall not exceed ten days (240 h).
OCSP_REFRESH_MAX = _dt.timedelta(days=10)

#: A/360 5.2.2.2: default OCSPRefresh used when a broadcaster does not state one.
OCSP_REFRESH_DEFAULT = _dt.timedelta(hours=20)


class Profile:
    """Certificate profiles (A/360 5.3.1)."""

    ROOT = "root"
    ISSUING_CA = "issuing-ca"
    TENANT_CA = "tenant-ca"
    SIGNALING_SIGNER = "signaling-signer"
    CDT_SIGNER = "cdt-signer"
    OCSP_RESPONDER = "ocsp-responder"

    CHOICES = [
        (ROOT, "Root CA (A/360 5.3.1.2)"),
        (ISSUING_CA, "Issuing CA (A/360 5.3.1.3)"),
        (TENANT_CA, "Tenant intermediate CA (A/360 5.3.1.3)"),
        (SIGNALING_SIGNER, "Broadcast signaling signer (A/360 5.3.1.6)"),
        (CDT_SIGNER, "CertificationData signer (A/360 5.2.2.2)"),
        (OCSP_RESPONDER, "OCSP responder (A/360 5.3.1.7)"),
    ]


class CertStatus:
    """Operational certificate status (the issuance authority is authoritative)."""

    GOOD = "good"
    REVOKED = "revoked"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"

    CHOICES = [
        (GOOD, "Good"),
        (REVOKED, "Revoked"),
        (EXPIRED, "Expired"),
        (SUPERSEDED, "Superseded by rollover"),
    ]


class KeyCustody:
    """How a certificate's private key is held.

    ``STANDARD`` wraps leaf keys under a per-tenant KMS CMK (the low-cost tier);
    ``PREMIUM`` keeps every leaf in a non-exportable KMS asymmetric key;
    ``LOCAL`` is the test/local development fallback only.
    """

    STANDARD = "standard"
    PREMIUM = "premium"
    LOCAL = "local"

    TENANT_CHOICES = [
        (STANDARD, "Standard (wrapped leaf keys)"),
        (PREMIUM, "Premium (KMS key per leaf)"),
    ]
    CERT_CHOICES = [
        (STANDARD, "Standard (wrapped leaf key)"),
        (PREMIUM, "Premium (KMS key)"),
        (LOCAL, "Local development key"),
    ]


class RolloverState:
    """State of a key-replacement plan (A/360 5.2.2.2 CertReplacement)."""

    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    CHOICES = [
        (PLANNED, "Planned"),
        (ACTIVE, "Active (window open)"),
        (COMPLETED, "Completed"),
        (CANCELLED, "Cancelled"),
    ]


class OrderStatus:
    """Order lifecycle: paid via Stripe, manually approved, then fulfilled.

    ``pending`` -> ``paid`` (webhook) -> ``fulfilled`` (manual approval issues
    the tenant CA + bundle).  ``cancelled``/``refunded`` are terminal.
    """

    PENDING = "pending"
    PAID = "paid"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

    CHOICES = [
        (PENDING, "Pending checkout"),
        (PAID, "Paid, awaiting approval"),
        (FULFILLED, "Fulfilled (issued + delivered)"),
        (CANCELLED, "Cancelled"),
        (REFUNDED, "Refunded"),
    ]
