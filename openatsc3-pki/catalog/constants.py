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
    SIGNALING_SIGNER = "signaling-signer"
    CDT_SIGNER = "cdt-signer"
    OCSP_RESPONDER = "ocsp-responder"

    CHOICES = [
        (ROOT, "Root CA (A/360 5.3.1.2)"),
        (ISSUING_CA, "Issuing CA (A/360 5.3.1.3)"),
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
