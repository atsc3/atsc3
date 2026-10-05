"""CertificationData LLS table (A/360 5.2.2.2) and LLS SignedMultiTable (A/331 6.7).

CertificationData (LLS_table_id 0x06) is a gzipped XML document, self-signed
(not inside a SignedMultiTable), carrying the end-entity signing
certificate(s), their CA chain, and a stapled OCSP response per certificate.
Its ``ToBeSignedData`` is signed by a key distinct from the CurrentCert/NextCert
signaling keys; the CMS signature spans the ``ToBeSignedData`` element
including its tags.

The SignedMultiTable (LLS_table_id 0x07) wraps one or more LLS tables and a
detached CMS signature computed over the bytes from ``LLS_payload_count`` up to
but not including ``signature_length``.  It is **not** gzipped.

Reference: ATSC A/360 5.2.2.2, 5.2.2.3; ATSC A/331 6.7 Table 6.15.
"""

from __future__ import annotations

import base64
import datetime as _dt
import gzip
import re
import struct
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from . import cms, keys
from .x509 import Certificate

#: A/360 5.2.2.2: the CDT XML namespace.
CDT_NAMESPACE = "tag:atsc.org,2016:XMLSchemas/ATSC3/Delivery/CDT/1.0/"

#: A/360 5.2.2.1: signing times are whole seconds.
_SIGNING_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Default OCSP validity window from producedAt (the published example uses
#: PT20H); A/360 5.2.2.2 @OCSPRefresh.
DEFAULT_OCSP_REFRESH = _dt.timedelta(hours=20)


def _b64_lines(der: bytes) -> str:
    """MIME base64 (64-char lines), matching the published CDT example."""
    raw = base64.b64encode(der).decode("ascii")
    return "\n".join(raw[i:i + 64] for i in range(0, len(raw), 64))


def _iso_duration(seconds: int) -> str:
    """An xs:dayTimeDuration for a whole number of seconds."""
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    out = "P"
    if days:
        out += f"{days}D"
    if hours or minutes:
        out += "T"
        if hours:
            out += f"{hours}H"
        if minutes:
            out += f"{minutes}M"
    return out or "PT0S"


def _parse_duration(text: str) -> _dt.timedelta:
    m = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", text.strip())
    if not m:
        raise ValueError(f"unsupported xs:dayTimeDuration {text!r}")
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return _dt.timedelta(days=days, hours=hours, minutes=minutes,
                         seconds=seconds)


@dataclass(frozen=True)
class CertificationData:
    """The pieces of a CertificationData LLS table.

    Attributes:
        certificates: the CA chain (issuing CA first) followed by the
            end-entity certificate(s); the root is **not** included (A/360
            5.2.2.2).
        current_cert: SubjectKeyIdentifier (DER bytes) of the certificate used
            to sign signaling messages.
        cmssigneddata: DER of the detached CMS SignedData over ``tobesigned``.
        ocsp_responses: one DER OCSPResponse per certificate.
        ocsp_refresh: validity window of the OCSP responses from ``producedAt``.
        tobesigned: the exact bytes the CMS signature covers (the
            ``ToBeSignedData`` element including tags).
        next_cert / next_cert_from / current_cert_until: the key-rollover
            window, when a CertReplacement element is present.
    """
    certificates: Tuple[bytes, ...]
    current_cert: bytes
    cmssigneddata: bytes
    ocsp_responses: Tuple[bytes, ...]
    ocsp_refresh: _dt.timedelta
    tobesigned: bytes
    next_cert: Optional[bytes] = None
    next_cert_from: Optional[_dt.datetime] = None
    current_cert_until: Optional[_dt.datetime] = None

    def _tobesigned_xml(self) -> List[str]:
        lines = [f'\t<ToBeSignedData OCSPRefresh="{_iso_duration(self.ocsp_refresh.total_seconds())}">']
        for c in self.certificates:
            lines.append("\t\t<Certificates>")
            lines.append(_b64_lines(c))
            lines.append("\t\t</Certificates>")
        lines.append("\t\t<CurrentCert>"
                     f"{base64.b64encode(self.current_cert).decode()}"
                     "</CurrentCert>")
        if self.next_cert is not None:
            lines.append(
                f'\t\t<CertReplacement NextCertFrom="{self.next_cert_from:{_SIGNING_TIME_FORMAT}}"'
                f' CurrentCertUntil="{self.current_cert_until:{_SIGNING_TIME_FORMAT}}">')
            lines.append("\t\t\t<NextCert>"
                         f"{base64.b64encode(self.next_cert).decode()}"
                         "</NextCert>")
            lines.append("\t\t</CertReplacement>")
        lines.append("\t</ToBeSignedData>")
        return lines

    def xml(self) -> str:
        """The CertificationData XML (uncompressed)."""
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 f'<CertificationData xmlns="{CDT_NAMESPACE}">']
        lines.extend(self._tobesigned_xml())
        lines.append("\t<CMSSignedData>"
                     f"{_b64_lines(self.cmssigneddata)}"
                     "</CMSSignedData>")
        for r in self.ocsp_responses:
            lines.append("\t<OCSPResponse>"
                         f"{_b64_lines(r)}"
                         "</OCSPResponse>")
        lines.append("</CertificationData>")
        return "\n".join(lines) + "\n"

    def tobesigned_bytes(self) -> bytes:
        """Recompute the exact signed ``ToBeSignedData`` element bytes."""
        return ("\n".join(self._tobesigned_xml())).encode("utf-8")

    def gzip_bytes(self) -> bytes:
        """The CDT LLS body: gzipped XML (A/360 5.2.2.2)."""
        return gzip.compress(self.xml().encode("utf-8"))


def build(
    chain: Sequence[bytes],
    signing_cert: Certificate,
    cdt_signer_cert: Certificate,
    cdt_signer_key: keys.KeyPair,
    ocsp_responses: Sequence[bytes],
    ocsp_refresh: _dt.timedelta = DEFAULT_OCSP_REFRESH,
    next_cert: Optional[Certificate] = None,
    next_cert_from: Optional[_dt.datetime] = None,
    current_cert_until: Optional[_dt.datetime] = None,
    signing_time: Optional[_dt.datetime] = None,
) -> CertificationData:
    """Assemble a CertificationData table (A/360 5.2.2.2).

    ``chain`` is the DER CA chain (issuing CA first) authenticating
    ``signing_cert``.  The CDT is signed by a *different* key from the
    signaling signers, ``cdt_signer_cert``/``cdt_signer_key`` (A/360 5.2.2.2).
    """
    placeholder = CertificationData(
        certificates=tuple(chain) + (signing_cert.der(), cdt_signer_cert.der()),
        current_cert=signing_cert.subject_key_identifier(),
        cmssigneddata=b"",
        ocsp_responses=tuple(ocsp_responses),
        ocsp_refresh=ocsp_refresh,
        tobesigned=b"",
        next_cert=(next_cert.subject_key_identifier() if next_cert else None),
        next_cert_from=next_cert_from,
        current_cert_until=current_cert_until,
    )
    tobesigned = placeholder.tobesigned_bytes()
    cms_der = cms.sign_detached(tobesigned, cdt_signer_cert, cdt_signer_key,
                                signing_time=signing_time)
    return CertificationData(
        certificates=placeholder.certificates,
        current_cert=placeholder.current_cert,
        cmssigneddata=cms_der,
        ocsp_responses=placeholder.ocsp_responses,
        ocsp_refresh=placeholder.ocsp_refresh,
        tobesigned=tobesigned,
        next_cert=placeholder.next_cert,
        next_cert_from=next_cert_from,
        current_cert_until=current_cert_until,
    )


@dataclass(frozen=True)
class LlsTable:
    """An LLS table body carried inside a SignedMultiTable (A/331 6.7)."""
    table_id: int
    version: int
    payload: bytes


@dataclass(frozen=True)
class ParsedSignedMultiTable:
    """A parsed SignedMultiTable: its tables and the signed CMS blob."""
    tables: Tuple[LlsTable, ...]
    cms: bytes
    signed_extent: bytes


def signed_multitable(tables: Sequence[LlsTable], signer_cert: Certificate,
                      signer_key: keys.KeyPair,
                      signing_time: Optional[_dt.datetime] = None) -> bytes:
    """Build an LLS SignedMultiTable (A/331 6.7 Table 6.15).

    The returned bytes are the LLS body for ``LLS_table_id`` 0x07: the
    ``SignedMultiTable()`` structure without the wrapping LLS header.  The CMS
    signature covers ``LLS_payload_count`` through the last payload.
    """
    parts = [struct.pack(">B", len(tables))]
    for t in tables:
        parts.append(struct.pack(">BBH", t.table_id, t.version, len(t.payload)))
        parts.append(t.payload)
    signed_extent = b"".join(parts)
    cms_der = cms.sign_detached(signed_extent, signer_cert, signer_key,
                                signing_time=signing_time)
    return signed_extent + struct.pack(">H", len(cms_der)) + cms_der


def parse_signed_multitable(data: bytes) -> ParsedSignedMultiTable:
    """Parse an LLS SignedMultiTable body (A/331 6.7)."""
    if not data:
        raise ValueError("empty SignedMultiTable")
    count = data[0]
    pos = 1
    tables = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise ValueError("truncated SignedMultiTable payload header")
        tid, ver, length = struct.unpack(">BBH", data[pos:pos + 4])
        pos += 4
        if pos + length > len(data):
            raise ValueError("truncated SignedMultiTable payload")
        tables.append(LlsTable(table_id=tid, version=ver,
                               payload=data[pos:pos + length]))
        pos += length
    signed_extent = data[:pos]
    if pos + 2 > len(data):
        raise ValueError("truncated SignedMultiTable signature_length")
    sig_len = struct.unpack(">H", data[pos:pos + 2])[0]
    pos += 2
    if pos + sig_len > len(data):
        raise ValueError("truncated SignedMultiTable signature")
    return ParsedSignedMultiTable(tables=tuple(tables),
                                  cms=data[pos:pos + sig_len],
                                  signed_extent=signed_extent)


def parse_certification_data(xml_or_gzip: bytes) -> CertificationData:
    """Parse a CertificationData LLS body (gzipped or raw XML).

    Returns the structural pieces; signature/chain/OCSP verification is done by
    the receiver-side verifier.
    """
    raw = xml_or_gzip
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    import xml.etree.ElementTree as ET
    root = ET.fromstring(raw)

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    certificates: List[bytes] = []
    current = None
    cms_blob = None
    ocsps: List[bytes] = []
    refresh = _dt.timedelta(0)
    next_cert = None
    next_from = None
    current_until = None
    for elem in root.iter():
        name = local(elem.tag)
        text = "".join((elem.text or "").split())
        if name == "ToBeSignedData":
            for k, v in elem.attrib.items():
                if local(k) == "OCSPRefresh":
                    refresh = _parse_duration(v)
        elif name == "Certificates":
            if text:
                certificates.append(_b64(text))
        elif name == "CurrentCert":
            current = _b64(text)
        elif name == "NextCert":
            next_cert = _b64(text)
        elif name == "CertReplacement":
            for k, v in elem.attrib.items():
                if local(k) == "NextCertFrom":
                    next_from = _dt.datetime.strptime(v, _SIGNING_TIME_FORMAT).replace(
                        tzinfo=_dt.timezone.utc)
                elif local(k) == "CurrentCertUntil":
                    current_until = _dt.datetime.strptime(v, _SIGNING_TIME_FORMAT).replace(
                        tzinfo=_dt.timezone.utc)
        elif name == "CMSSignedData":
            cms_blob = _b64(text)
        elif name == "OCSPResponse":
            ocsps.append(_b64(text))
    if cms_blob is None or current is None:
        raise ValueError("CertificationData missing CMSSignedData or CurrentCert")
    return CertificationData(
        certificates=tuple(certificates), current_cert=current,
        cmssigneddata=cms_blob, ocsp_responses=tuple(ocsps),
        ocsp_refresh=refresh, tobesigned=b"", next_cert=next_cert,
        next_cert_from=next_from, current_cert_until=current_until)


def _b64(text: str) -> bytes:
    return base64.b64decode("".join(text.split()))
