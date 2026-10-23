"""Ceremony minutes and root-rotation distribution bundles.

The offline root is used so rarely that every use is a witnessed ceremony.  Two
artifacts support that practice:

* **Minutes** -- a tamper-evident transcript that binds the inputs and outputs of
  an operation by SHA-256.  The record carries a self-``digest`` (recomputed on
  verification, so any edit is detected) and may carry a detached ECDSA
  ``signature`` made through the :class:`~openatsc3_pki.signer.Signer` seam.  It
  is the record a witness attests to and that the online side files alongside
  the imported certificate.

* **Rotation bundle** -- the files a trust store needs to move to a new root: the
  new root's self-signed certificate (the new anchor) and its cross-signed bridge
  under the outgoing root (:func:`~openatsc3_pki.x509.cross_sign_root`), with a
  hashed manifest.  A verifier that still pins only the old anchor can path-build
  old -> bridge -> new-root-issued certificates until the new anchor ships.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence

from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec

from . import signer as _signer, x509

#: Filename of the rotation manifest inside a bundle directory.
MANIFEST_NAME = "MANIFEST.json"

#: The minutes format version written by this module.
FORMAT_VERSION = 1


def _canonical(record: Mapping) -> bytes:
    """Deterministic JSON bytes for digesting/signing (sorted, compact)."""
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode()


def cert_fingerprint(cert: x509.Certificate) -> dict:
    """The identifying facts of a certificate, for the minutes."""
    return {
        "subject": cert.cert.subject.rfc4514_string(),
        "issuer": cert.cert.issuer.rfc4514_string(),
        "serial": str(cert.cert.serial_number),
        "ski": cert.subject_key_identifier().hex(),
        "sha256": hashlib.sha256(cert.der()).hexdigest(),
    }


def build_minutes(op: str, *, artifacts: Optional[Mapping[str, bytes]] = None,
                  certs: Optional[Mapping[str, x509.Certificate]] = None,
                  witnesses: Sequence[str] = (), note: str = "",
                  created: Optional[str] = None) -> dict:
    """Build an unsigned minutes record with a self-``digest``.

    ``artifacts`` maps a label to the raw bytes (e.g. the request and the signed
    result); ``certs`` maps a label to a parsed certificate.  Only hashes are
    stored, so the record is safe to file with the ceremony evidence.
    """
    record = {
        "version": FORMAT_VERSION,
        "op": op,
        "created": created or datetime.now(timezone.utc).isoformat(),
        "witnesses": list(witnesses),
        "note": note,
        "artifacts": {name: hashlib.sha256(data).hexdigest()
                      for name, data in (artifacts or {}).items()},
        "certificates": {name: cert_fingerprint(cert)
                         for name, cert in (certs or {}).items()},
    }
    record["digest"] = hashlib.sha256(_canonical(record)).hexdigest()
    return record


def sign_minutes(record: Mapping, key) -> dict:
    """Return a copy of ``record`` with a detached ECDSA ``signature``.

    ``key`` is a local :class:`~openatsc3_pki.keys.KeyPair` or any
    :class:`~openatsc3_pki.signer.Signer`; the signature covers the record
    without its ``signature`` field, so it verifies against
    :func:`verify_minutes`.
    """
    signer = _signer.as_signer(key)
    hash_name = _signer.hash_for_curve(signer.curve)
    value = signer.sign(_canonical(_without(record, "signature")), hash_name)
    signed = dict(record)
    signed["signature"] = {
        "algorithm": f"ECDSA-{hash_name}",
        "curve": signer.curve,
        "value": base64.b64encode(value).decode("ascii"),
    }
    return signed


def verify_minutes(record: Mapping, issuer: Optional[x509.Certificate] = None) -> dict:
    """Verify a minutes record and return it.

    Raises ``ValueError`` when the self-``digest`` does not match (the record was
    edited) or, when a ``signature`` is present, when ``issuer`` is missing or the
    signature does not verify under the issuer's public key.
    """
    expected = hashlib.sha256(
        _canonical(_without(record, "digest", "signature"))).hexdigest()
    if record.get("digest") != expected:
        raise ValueError("minutes digest mismatch (record was modified)")
    signature = record.get("signature")
    if signature is not None:
        if issuer is None:
            raise ValueError("minutes are signed; pass the issuer certificate")
        public = issuer.cert.public_key()
        if not isinstance(public, ec.EllipticCurvePublicKey):
            raise ValueError("issuer must be an EC certificate")
        hash_name = _signer.hash_for_curve(public.curve.name)
        try:
            public.verify(base64.b64decode(signature["value"]),
                          _canonical(_without(record, "signature")),
                          ec.ECDSA(_signer.HASH_ALGORITHMS[hash_name]()))
        except InvalidSignature:
            raise ValueError("minutes signature does not verify") from None
    return dict(record)


def rotation_bundle(new_root: x509.Certificate, cross: x509.Certificate,
                    old_root: Optional[x509.Certificate] = None,
                    *, witnesses: Sequence[str] = (),
                    created: Optional[str] = None) -> dict:
    """The files a trust store needs to rotate to ``new_root``.

    Returns a mapping of filename to bytes: the new root's self-signed anchor,
    its cross-signed bridge under the outgoing root, the outgoing anchor (when
    supplied), and a hashed ``MANIFEST.json`` binding them.
    """
    if not x509._self_signed(new_root):
        raise ValueError("new root must be self-signed")
    if x509._public_der(new_root.cert.public_key()) != x509._public_der(
            cross.cert.public_key()):
        raise ValueError("cross certificate does not carry the new root key")
    if old_root is not None:
        if cross.cert.issuer != old_root.cert.subject:
            raise ValueError("cross certificate is not issued by the old root")
        from . import verify as _verify
        if not _verify.signed_by(cross, old_root):
            raise ValueError("cross certificate is not signed by the old root")
    artifacts = {
        "new-root.cert.pem": new_root.pem(),
        "new-root-cross.pem": cross.pem(),
    }
    certs = {"new-root": new_root, "cross": cross}
    if old_root is not None:
        artifacts["old-root.cert.pem"] = old_root.pem()
        certs["old-root"] = old_root
    manifest = build_minutes("root-rotation", artifacts=artifacts, certs=certs,
                             witnesses=witnesses, created=created)
    artifacts[MANIFEST_NAME] = (json.dumps(manifest, indent=2) + "\n").encode()
    return artifacts


def _without(record: Mapping, *fields: str) -> dict:
    drop = set(fields)
    return {k: v for k, v in record.items() if k not in drop}
