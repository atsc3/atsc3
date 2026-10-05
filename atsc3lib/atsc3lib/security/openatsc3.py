"""Built-in security provider: our own greenfield CA (``openatsc3_pki``).

This adapts the ``openatsc3_pki`` package to the :class:`SecurityProvider`
protocol.  It is the default provider and is imported lazily: when the package
is absent the receiver still runs and verification reports that no provider is
available.  An A3SA/Widevine-backed provider is a drop-in replacement.

Reference: ATSC A/360 5.2.2.2 (CertificationData), 5.2.2.3 (LLS signatures),
5.2.2.6 (verification tasks); ATSC A/331 6.7 (SignedMultiTable).
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .base import SecurityProvider, SignableTable, TableVerdict, register


def _require_pki():
    try:
        import openatsc3_pki
    except Exception as exc:  # pragma: no cover - exercised without the extra
        raise RuntimeError(
            "the 'openatsc3' security provider needs the openatsc3-pki package "
            "(pip install -e .[pki])") from exc
    return openatsc3_pki


class OpenAtsc3Provider:
    """Security provider backed by the greenfield ``openatsc3_pki`` CA."""

    #: Registry name.
    name = "openatsc3"

    def load_trust_anchors(self, paths: Sequence[str]) -> list:
        """Load trusted root certificates from PEM files (fail-closed)."""
        pki = _require_pki()
        return pki.verify.load_trust_roots(paths)

    def verify_certification_data(self, table: SignableTable, anchors: Sequence,
                                  now=None) -> Tuple[TableVerdict, object]:
        """Verify an LLS CertificationData (0x06) table (A/360 5.2.2.6)."""
        pki = _require_pki()
        try:
            parsed = pki.cdt.parse_certification_data(table.data)
        except Exception as exc:
            return TableVerdict(table.table_id, table.name, False,
                                f"cannot parse CertificationData: {exc}"), None
        verdict, certified = pki.verify.verify_certification_data(
            parsed, anchors, now=now)
        return TableVerdict(table.table_id, table.name, verdict.ok,
                            verdict.reason), certified

    def verify_signed_table(self, table: SignableTable, handle, *,
                            expected_bsids: Optional[Sequence[int]] = None,
                            now=None, previous_signing_time=None) -> TableVerdict:
        """Verify a signed table (SignedMultiTable 0x07, A/331 6.7)."""
        pki = _require_pki()
        if handle is None:
            return TableVerdict(table.table_id, table.name, False,
                                "no verified CertificationData handle")
        try:
            multi = pki.cdt.parse_signed_multitable(table.data)
        except Exception as exc:
            return TableVerdict(table.table_id, table.name, False,
                                f"cannot parse SignedMultiTable: {exc}")
        verdict = pki.verify.verify_signed_message(
            multi.cms, multi.signed_extent, handle,
            expected_bsids or (), now=now,
            previous_signing_time=previous_signing_time)
        signing_time = None
        signer_ski = None
        try:
            sig = pki.cms.parse(multi.cms)
            signing_time = sig.signing_time
            signer_ski = sig.signer_ski
        except Exception:
            pass
        return TableVerdict(table.table_id, table.name, verdict.ok,
                            verdict.reason, signing_time=signing_time,
                            signer_ski=signer_ski)


register(OpenAtsc3Provider())
