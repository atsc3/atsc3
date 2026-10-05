"""Receiver-side verification of ATSC A/360 signed signaling.

Implements the checks A/360 5.2.2.6 mandates for a CertificationData table and
for a signed signaling message (an LLS SignedMultiTable in particular).  The
trust anchor is a caller-supplied set of root certificates; the policy is
fail-closed (a chain that does not reach a trusted root is invalid).

CertificationData tasks (A/360 5.2.2.6, "CertificationData"):

1. every certificate chain reaches a trusted root;
2. every certificate is within its validity period;
3. the ``CMSSignedData`` signature is valid and authenticated by a certificate
   chain carried in the message;
4. the CDT signing key differs from the CurrentCert/NextCert signaling keys;
5. every certificate has a matching, authentic, ``good`` OCSP response whose
   ``producedAt`` is not more than 1 hour in the future, before ``nextUpdate``,
   not more than 10 days old, and before ``producedAt + OCSPRefresh``.

Signaling-message tasks (A/360 5.2.2.6):

1. the signature verifies over the signed extent;
2. ``SigningTime`` is not in the future and not older than the previous
   instance of the same message type;
3. the signing key is authenticated by an unexpired end-entity certificate in
   the CDT: it has ``id-atsc-kp-signalingSigning`` EKU, an
   ``id-atsc-sdattr-bsid`` SDA whose value set equals the SLT's bsid set, a
   SubjectKeyIdentifier matching CurrentCert (or NextCert), and a valid period;
4. the signing time is within the CurrentCertUntil / NextCertFrom window;
5. the OCSP response for that certificate is good and fresh.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from cryptography import x509 as _x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509 import ocsp as _ocsp
from cryptography.x509.oid import ExtendedKeyUsageOID

from . import cms, oids
from .x509 import Certificate


@dataclass(frozen=True)
class Verdict:
    """The outcome of a verification step."""
    ok: bool
    reason: str = ""
    detail: str = ""


def _now(now: Optional[_dt.datetime]) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def _check_validity(cert: Certificate, now: _dt.datetime) -> Optional[str]:
    if now < cert.cert.not_valid_before_utc:
        return f"certificate {cert.subject.rfc4514_string()} not yet valid"
    if now > cert.cert.not_valid_after_utc:
        return f"certificate {cert.subject.rfc4514_string()} expired"
    return None


def _verify_signed_by(cert: Certificate, issuer: Certificate) -> bool:
    """Verify ``cert`` was signed by ``issuer`` (RFC 5280 signature)."""
    pub = issuer.cert.public_key()
    data = cert.cert.tbs_certificate_bytes
    sig = cert.cert.signature
    h = cert.cert.signature_hash_algorithm
    try:
        if isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(sig, data, ec.ECDSA(h))
        elif isinstance(pub, rsa.RSAPublicKey):
            pub.verify(sig, data, padding.PKCS1v15(), h)
        else:
            return False
    except InvalidSignature:
        return False
    return True


@dataclass(frozen=True)
class _Chain:
    """A leaf-to-root certificate path and the roots it reaches."""
    path: Tuple[Certificate, ...]
    root: Certificate


def _find_chain(leaf: Certificate, roots: Sequence[Certificate],
                pool: Sequence[Certificate]) -> Optional[_Chain]:
    """Build a path from ``leaf`` to a trusted root, if one exists."""
    by_subject: Dict[bytes, List[Certificate]] = {}
    for c in list(pool) + list(roots):
        by_subject.setdefault(c.cert.subject.public_bytes(), []).append(c)
    root_subjects = {r.cert.subject.public_bytes() for r in roots}

    def rec(current: Certificate, seen) -> Optional[Tuple[Certificate, ...]]:
        if current.cert.subject.public_bytes() in root_subjects:
            for r in roots:
                if r.cert.subject == current.cert.subject:
                    return (current,)
        for parent in by_subject.get(current.cert.issuer.public_bytes(), []):
            if parent.subject == current.subject:
                continue
            key = parent.cert.serial_number
            if key in seen:
                continue
            sub = rec(parent, seen | {key})
            if sub is not None:
                return (current,) + sub
        return None

    path = rec(leaf, {leaf.cert.serial_number})
    if path is None:
        return None
    root = path[-1]
    if not _verify_signed_by(root, root):
        return None
    for child, parent in zip(path, path[1:]):
        if not _verify_signed_by(child, parent):
            return None
    return _Chain(path=path, root=root)


def _verify_ocsp(der: bytes, cert: Certificate, issuer: Certificate,
                 now: _dt.datetime, refresh: _dt.timedelta) -> Optional[str]:
    """Validate one OCSP response for ``cert`` (A/360 5.2.2.6 item 5)."""
    try:
        resp = _ocsp.load_der_ocsp_response(der)
    except Exception as exc:  # malformed
        return f"malformed OCSP response: {exc}"
    if resp.response_status is not _ocsp.OCSPResponseStatus.SUCCESSFUL:
        return "OCSP response status not successful"
    single = next((r for r in resp.responses
                   if r.serial_number == cert.cert.serial_number), None)
    if single is None:
        return "no OCSP response matches certificate serial"
    if single.certificate_status is not _ocsp.OCSPCertStatus.GOOD:
        return "certificate status is not good"
    if resp.signature_algorithm_oid is None:
        return "OCSP response has no signature"
    responder = next((Certificate(c) for c in (resp.certificates or [])
                       if c.subject == resp.responder_name), None)
    if responder is None and resp.certificates:
        responder = Certificate(resp.certificates[0])
    if responder is None:
        return "OCSP response carries no responder certificate"
    # The responder must authenticate: it is the issuer itself, or a
    # delegated responder with id-kp-OCSPSigning signed by the issuer
    # (RFC 6960 4.2.2.2).
    if responder.subject != issuer.subject:
        try:
            eku = responder.cert.extensions.get_extension_for_class(
                _x509.ExtendedKeyUsage).value
        except _x509.ExtensionNotFound:
            return "OCSP responder is not authorized (no OCSPSigning EKU)"
        if ExtendedKeyUsageOID.OCSP_SIGNING not in eku:
            return "OCSP responder lacks id-kp-OCSPSigning"
        if not _verify_signed_by(responder, issuer):
            return "OCSP responder certificate is not signed by the issuer"
    try:
        pub = responder.cert.public_key()
        pub.verify(resp.signature, resp.tbs_response_bytes,
                   ec.ECDSA(resp.signature_hash_algorithm)
                   if isinstance(pub, ec.EllipticCurvePublicKey)
                   else padding.PKCS1v15())
    except (InvalidSignature, TypeError):
        return "OCSP response signature does not verify"
    produced = resp.produced_at_utc
    if produced is None:
        return "OCSP response has no producedAt"
    if now < produced - _dt.timedelta(hours=oids.OCSP_SKEW_HOURS):
        return "OCSP producedAt is in the future"
    single_update = resp.this_update_utc
    if single_update is not None and now < single_update - _dt.timedelta(
            hours=oids.OCSP_SKEW_HOURS):
        return "OCSP thisUpdate is in the future"
    if resp.next_update_utc is not None and now > resp.next_update_utc:
        return "OCSP response expired (nextUpdate)"
    if now > produced + _dt.timedelta(days=oids.OCSP_MAX_AGE_DAYS):
        return "OCSP response older than 10 days"
    if refresh and now > produced + refresh:
        return "OCSP response older than OCSPRefresh"
    return None


@dataclass(frozen=True)
class CertifiedKeys:
    """Verified CDT state a later signaling message is checked against."""
    cdt: object
    current_ski: bytes
    next_ski: Optional[bytes]
    current_until: Optional[_dt.datetime]
    next_from: Optional[_dt.datetime]
    certificates: Tuple[Certificate, ...]
    ocsp_refresh: _dt.timedelta
    #: SKI -> (certificate, chain)
    verified: Dict[bytes, _Chain] = field(default_factory=dict)


def verify_certification_data(cdt, trust_roots: Sequence[Certificate],
                              now: Optional[_dt.datetime] = None
                              ) -> Tuple[Verdict, Optional[CertifiedKeys]]:
    """Run the A/360 5.2.2.6 CertificationData checks.

    ``cdt`` is a :class:`openatsc3_pki.cdt.CertificationData` (parsed or
    built).  ``trust_roots`` are the receiver's trusted root certificates;
    the check fails closed when no path reaches one of them.
    """
    now = _now(now)
    certs = [Certificate(_x509.load_der_x509_certificate(d))
             for d in cdt.certificates]
    by_ski = {c.subject_key_identifier(): c for c in certs}
    if cdt.current_cert not in by_ski:
        return Verdict(False, "CurrentCert SKI is not among Certificates"), None

    # 1-2: every certificate chains to a trusted root and is in-date.
    verified: Dict[bytes, _Chain] = {}
    for c in certs:
        reason = _check_validity(c, now)
        if reason:
            return Verdict(False, reason), None
        chain = _find_chain(c, trust_roots, certs)
        if chain is None:
            return Verdict(
                False,
                f"certificate {c.subject.rfc4514_string()} does not chain to "
                f"a trusted root"), None
        verified[c.subject_key_identifier()] = chain

    # 3: the CMS signature verifies and is authenticated by a message cert.
    signed = cdt.tobesigned or _recompute_tobesigned(cdt)
    sig = None
    try:
        sig = cms.parse(cdt.cmssigneddata)
    except Exception as exc:
        return Verdict(False, f"cannot parse CMSSignedData: {exc}"), None
    cdt_signer = by_ski.get(sig.signer_ski)
    if cdt_signer is None:
        return Verdict(False, "CMSSignedData signer SKI not in Certificates"), None
    if not cms.verify(cdt.cmssigneddata, signed, cdt_signer.cert.public_key()):
        return Verdict(False, "CertificationData CMSSignedData signature invalid"), None

    # 4: the CDT signing key differs from the signaling keys.
    current = by_ski[cdt.current_cert]
    if _same_public_key(cdt_signer, current):
        return Verdict(False, "CDT signing key equals CurrentCert key"), None
    if cdt.next_cert and _same_public_key(cdt_signer, by_ski.get(cdt.next_cert)):
        return Verdict(False, "CDT signing key equals NextCert key"), None

    # 5: a matching, authentic, good, fresh OCSP response per certificate.
    if len(cdt.ocsp_responses) < len(certs):
        return Verdict(False, "missing OCSP response for some certificate"), None
    ocsp_by_serial: Dict[int, bytes] = {}
    for der in cdt.ocsp_responses:
        try:
            r = _ocsp.load_der_ocsp_response(der)
        except Exception:
            continue
        for single in r.responses:
            ocsp_by_serial.setdefault(single.serial_number, der)
    for c in certs:
        der = ocsp_by_serial.get(c.cert.serial_number)
        if der is None:
            return Verdict(False,
                           f"no OCSP response for serial {c.cert.serial_number}"), None
        issuer = by_ski.get(_issuer_ski(cert=c, certs=certs, verified=verified))
        reason = _verify_ocsp(der, c, issuer or c, now, cdt.ocsp_refresh)
        if reason:
            return Verdict(False, reason), None

    keys = CertifiedKeys(
        cdt=cdt, current_ski=cdt.current_cert, next_ski=cdt.next_cert,
        current_until=cdt.current_cert_until, next_from=cdt.next_cert_from,
        certificates=tuple(certs), ocsp_refresh=cdt.ocsp_refresh,
        verified=verified)
    return Verdict(True, "CertificationData verified"), keys


def _recompute_tobesigned(cdt) -> bytes:
    return cdt.tobesigned_bytes()


def _same_public_key(a: Certificate, b: Optional[Certificate]) -> bool:
    if b is None:
        return False
    from cryptography.hazmat.primitives import serialization
    pa = a.cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    pb = b.cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    return pa == pb


def _issuer_ski(cert: Certificate, certs: Sequence[Certificate],
                verified: Dict[bytes, _Chain]) -> Optional[bytes]:
    chain = verified.get(cert.subject_key_identifier())
    if chain and len(chain.path) > 1:
        return chain.path[1].subject_key_identifier()
    return None


def verify_signed_message(cms_der: bytes, signed_extent: bytes, keys: CertifiedKeys,
                          slt_bsids: Iterable[int],
                          now: Optional[_dt.datetime] = None,
                          previous_signing_time: Optional[_dt.datetime] = None,
                          require_next: bool = False) -> Verdict:
    """Run the A/360 5.2.2.6 signaling-message checks against a verified CDT."""
    now = _now(now)
    try:
        sig = cms.parse(cms_der)
    except Exception as exc:
        return Verdict(False, f"cannot parse signature: {exc}")

    # 3c: the signing cert's SKI matches CurrentCert or NextCert.
    by_ski = {c.subject_key_identifier(): c for c in keys.certificates}
    signer = by_ski.get(sig.signer_ski)
    if signer is None:
        return Verdict(False, "signer SKI not in CertificationData")
    if sig.signer_ski != keys.current_ski and sig.signer_ski != keys.next_ski:
        return Verdict(False, "signer is neither CurrentCert nor NextCert")
    if require_next and sig.signer_ski != keys.next_ski:
        return Verdict(False, "message must be signed by NextCert")

    # 2: SigningTime is not in the future and not older than the previous one.
    if sig.signing_time > now + _dt.timedelta(minutes=1):
        return Verdict(False, "SigningTime is in the future")
    if previous_signing_time is not None and sig.signing_time < previous_signing_time:
        return Verdict(False, "SigningTime went backward")

    # 1: the signature verifies.
    if not cms.verify(cms_der, signed_extent, signer.cert.public_key()):
        return Verdict(False, "signaling signature invalid")

    # 3a: id-atsc-kp-signalingSigning EKU.
    if not signer.signaling_eku():
        return Verdict(False, "signer lacks id-atsc-kp-signalingSigning EKU")

    # 3b: the SDA bsid set equals the SLT's.
    cert_bsids = signer.bsids()
    if tuple(sorted(int(b) for b in slt_bsids)) != tuple(sorted(cert_bsids)):
        return Verdict(False,
                       f"signer bsid set {cert_bsids} != SLT bsid set "
                       f"{tuple(sorted(int(b) for b in slt_bsids))}")

    # 3d: the signer is unexpired.
    reason = _check_validity(signer, now)
    if reason:
        return Verdict(False, reason)

    # 4: the signing time is within the CurrentCertUntil / NextCertFrom window.
    if sig.signer_ski == keys.current_ski and keys.current_until is not None:
        if sig.signing_time > keys.current_until:
            return Verdict(False, "SigningTime after CurrentCertUntil")
    if sig.signer_ski == keys.next_ski and keys.next_from is not None:
        if sig.signing_time < keys.next_from:
            return Verdict(False, "SigningTime before NextCertFrom")

    # 5: the OCSP response for the signer is good and fresh.
    ocsp_der = _ocsp_for(keys, signer.cert.serial_number)
    if ocsp_der is None:
        return Verdict(False, "no OCSP response for signer")
    issuer = _issuer_cert(keys, signer)
    reason = _verify_ocsp(ocsp_der, signer, issuer or signer, now,
                          keys.ocsp_refresh)
    if reason:
        return Verdict(False, reason)

    return Verdict(True, "signature verified")


def _ocsp_for(keys: CertifiedKeys, serial: int) -> Optional[bytes]:
    for der in keys.cdt.ocsp_responses:
        try:
            r = _ocsp.load_der_ocsp_response(der)
        except Exception:
            continue
        if any(s.serial_number == serial for s in r.responses):
            return der
    return None


def _issuer_cert(keys: CertifiedKeys, cert: Certificate) -> Optional[Certificate]:
    chain = keys.verified.get(cert.subject_key_identifier())
    if chain and len(chain.path) > 1:
        return chain.path[1]
    return None


def load_trust_roots(paths: Sequence[str]) -> List[Certificate]:
    """Load trusted root certificates from PEM files."""
    out = []
    for p in paths:
        with open(p, "rb") as fh:
            data = fh.read()
        for cert in _x509.load_pem_x509_certificates(data):
            out.append(Certificate(cert))
    return out
