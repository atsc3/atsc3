"""ATSC 3.0 receiver-side security gate (provider-agnostic).

The receiver verifies signed signaling through a pluggable
:class:`~atsc3lib.security.base.SecurityProvider`.  This package keeps the
public helpers stable while the crypto backend can be swapped; the default is
our own greenfield CA (``openatsc3_pki``), and an A3SA/Widevine provider can be
registered instead.

The receiver trusts only its configured anchors (fail-closed).  No receivable
stream on the lighthouse RF33 multiplex carries our signature, so this path is
exercised on synthesized A/331-layer tables (see ``openatsc3-pki``).
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .base import (
    CDT_TABLE_ID,
    SIGNED_MULTITABLE_ID,
    SecurityProvider,
    SecurityReport,
    SignableTable,
    TableVerdict,
    available,
    get_provider,
    register,
    set_provider,
)

__all__ = [
    "CDT_TABLE_ID",
    "SIGNED_MULTITABLE_ID",
    "SecurityProvider",
    "SecurityReport",
    "SignableTable",
    "TableVerdict",
    "available",
    "get_provider",
    "register",
    "set_provider",
    "load_trust_roots",
    "verify_streams",
    "describe",
]


def _signable(lls_table) -> SignableTable:
    """Adapt an ``atsc3lib`` LLS table to the provider's generic row."""
    return SignableTable(
        table_id=lls_table.table_id,
        name=getattr(lls_table, "name", f"0x{lls_table.table_id:02x}"),
        data=lls_table.data,
        version=getattr(lls_table, "table_version", 0))


def load_trust_roots(paths: Sequence[str], provider=None) -> list:
    """Load trusted root certificates from PEM files (fail-closed store)."""
    return get_provider(provider).load_trust_anchors(paths)


def verify_streams(streams, trust_roots: Sequence, now=None,
                   slt_bsids: Optional[Sequence[int]] = None,
                   provider=None) -> SecurityReport:
    """Verify the LLS security tables carried in a PLP's decoded streams.

    ``streams`` is a :class:`~atsc3lib.payload.DecodedStreams`.  ``trust_roots``
    are the receiver's trust anchors (see :func:`load_trust_roots`).
    ``slt_bsids`` defaults to the parsed SLT's bsid set, which A/360 5.2.2.6
    item 3b requires the signer's ``id-atsc-sdattr-bsid`` to equal.  ``provider``
    selects a registered provider by name (default: the active one).
    """
    backend = get_provider(provider)
    report = SecurityReport(provider=getattr(backend, "name", ""))

    lls = list(getattr(streams, "lls", []) or [])
    cdt_tables = [t for t in lls if t.table_id == CDT_TABLE_ID]
    signed = [t for t in lls if t.table_id == SIGNED_MULTITABLE_ID]

    if not cdt_tables:
        report.reason = "no CertificationData (0x06) table present"
        return _attach(streams, report)

    if slt_bsids is None:
        slt = getattr(streams, "slt", None)
        slt_bsids = getattr(slt, "bsid", ()) if slt is not None else ()

    verdict, handle = backend.verify_certification_data(
        _signable(cdt_tables[0]), trust_roots, now=now)
    report.cdt_ok = verdict.ok
    report.handle = handle
    if not verdict.ok:
        report.reason = verdict.reason
        return _attach(streams, report)

    previous = None
    for table in signed:
        # A/360 5.2.2.6 item 2b: SigningTime must not go backward for the same
        # message type.
        v = backend.verify_signed_table(
            _signable(table), handle, expected_bsids=slt_bsids, now=now,
            previous_signing_time=previous)
        report.verdicts.append(v)
        if v.signing_time is not None:
            previous = v.signing_time

    if not signed:
        report.reason = "no SignedMultiTable (0x07) present"
    if not report.ok and not report.reason:
        report.reason = describe(report)
    return _attach(streams, report)


def _attach(streams, report: SecurityReport) -> SecurityReport:
    try:
        streams.verification = report
    except Exception:
        pass
    return report


def describe(report: SecurityReport) -> str:
    """A one-line human summary of a :class:`SecurityReport`."""
    if report.ok:
        return (f"signed signaling verified "
                f"({len(report.verdicts)} table(s))")
    if not report.cdt_ok:
        return f"CertificationData NOT verified: {report.reason}"
    for v in report.verdicts:
        if not v.ok:
            return f"SignedMultiTable (0x{v.table_id:02x}) NOT verified: {v.reason}"
    return report.reason or "not verified"
