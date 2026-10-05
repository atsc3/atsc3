"""ATSC A/360 certificate profiles: root, issuing CA, signaling signer.

Profiles follow A/360 5.3.1:

* Root (5.3.1.2): ECDSA >= P-384; CA; ``keyCertSign`` + ``crlSign``.
* Issuing CA (5.3.1.3): ECDSA >= P-256; CA.
* Broadcast signaling signer (5.3.1.6): ECDSA >= P-256; KeyUsage critical
  ``digitalSignature`` **only**; ExtendedKeyUsage critical containing
  ``id-atsc-kp-signalingSigning`` (1.3.6.1.4.1.51552.37.3); Subject Directory
  Attributes ``id-atsc-sdattr-bsid`` holding the SET OF INTEGER bsids.
* OCSP responder (5.3.1.7): ECDSA >= P-256; ``id-kp-OCSPSigning``.

The subject DN is modelled on the published ATSC CertificationData example
(``CN``, ``OU``, ``O``, ``L``, ``ST``, ``C``).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Iterable, Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

from . import keys
from .oids import ID_ATSC_KP_SIGNALING_SIGNING, ID_KP_OCSP_SIGNING
from .sda import encode_bsid_sda


@dataclass(frozen=True)
class SubjectInfo:
    """An X.509 subject distinguished name (RFC 5280)."""
    common_name: str
    organizational_unit: str
    organization: str
    locality: str = ""
    state: str = ""
    country: str = "US"

    def name(self) -> x509.Name:
        parts = [
            x509.NameAttribute(NameOID.COUNTRY_NAME, self.country),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, self.organization),
        ]
        if self.organizational_unit:
            parts.append(
                x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,
                                   self.organizational_unit))
        if self.state:
            parts.append(x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME,
                                            self.state))
        if self.locality:
            parts.append(x509.NameAttribute(NameOID.LOCALITY_NAME, self.locality))
        parts.append(x509.NameAttribute(NameOID.COMMON_NAME, self.common_name))
        return x509.Name(parts)


def _hash_for(key: keys.KeyPair) -> hashes.HashAlgorithm:
    """A/360 5.2.2.1 item 4: permitted curve/digest pair."""
    return {"secp256r1": hashes.SHA256(),
            "secp384r1": hashes.SHA384(),
            "secp521r1": hashes.SHA512()}[key.curve]


def _serial() -> int:
    import secrets
    return x509.random_serial_number()


def issue_root(subject: SubjectInfo, key: keys.KeyPair,
               days: int = 7300) -> "Certificate":
    """Self-signed root certificate (A/360 5.3.1.2)."""
    if key.curve != keys.ROOT_CURVE:
        raise ValueError(f"root must be {keys.ROOT_CURVE} (A/360 5.3.1.2)")
    now = _dt.datetime.now(_dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject.name())
        .issuer_name(subject.name())
        .public_key(key.public)
        .serial_number(_serial())
        .not_valid_before(now - _dt.timedelta(minutes=1))
        .not_valid_after(now + _dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                       critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=False, content_commitment=False,
                          key_encipherment=False, data_encipherment=False,
                          key_agreement=False, key_cert_sign=True,
                          crl_sign=True, encipher_only=False,
                          decipher_only=False),
            critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public),
                       critical=False)
    )
    return _sign(builder, key)


def issue_ca(subject: SubjectInfo, key: keys.KeyPair, issuer: "Certificate",
             issuer_key: keys.KeyPair, days: int = 3650) -> "Certificate":
    """Issuing CA certificate (A/360 5.3.1.3), signed by ``issuer``."""
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject.name())
        .issuer_name(issuer.cert.subject)
        .public_key(key.public)
        .serial_number(_serial())
        .not_valid_before(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=1))
        .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                       critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=False, content_commitment=False,
                          key_encipherment=False, data_encipherment=False,
                          key_agreement=False, key_cert_sign=True,
                          crl_sign=True, encipher_only=False,
                          decipher_only=False),
            critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public),
                       critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public),
            critical=False)
    )
    return _sign(builder, issuer_key)


def issue_signaling_signer(subject: SubjectInfo, key: keys.KeyPair,
                           issuer: "Certificate", issuer_key: keys.KeyPair,
                           bsids: Iterable[int], days: int = 365) -> "Certificate":
    """Broadcast signaling signer certificate (A/360 5.3.1.6)."""
    bsid_set = tuple(sorted(int(b) for b in bsids))
    if not bsid_set:
        raise ValueError("a signaling signer must list at least one bsid")
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject.name())
        .issuer_name(issuer.cert.subject)
        .public_key(key.public)
        .serial_number(_serial())
        .not_valid_before(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=1))
        .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, content_commitment=False,
                          key_encipherment=False, data_encipherment=False,
                          key_agreement=False, key_cert_sign=False,
                          crl_sign=False, encipher_only=False,
                          decipher_only=False),
            critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.ObjectIdentifier(ID_ATSC_KP_SIGNALING_SIGNING)]),
            critical=True)
        .add_extension(
            x509.UnrecognizedExtension(
                ExtensionOID.SUBJECT_DIRECTORY_ATTRIBUTES,
                encode_bsid_sda(bsid_set)),
            critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public),
                       critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public),
            critical=False)
    )
    return _sign(builder, issuer_key)


def issue_ocsp_responder(subject: SubjectInfo, key: keys.KeyPair,
                         issuer: "Certificate", issuer_key: keys.KeyPair,
                         days: int = 365) -> "Certificate":
    """OCSP responder certificate (A/360 5.3.1.7)."""
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject.name())
        .issuer_name(issuer.cert.subject)
        .public_key(key.public)
        .serial_number(_serial())
        .not_valid_before(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=1))
        .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, content_commitment=False,
                          key_encipherment=False, data_encipherment=False,
                          key_agreement=False, key_cert_sign=False,
                          crl_sign=False, encipher_only=False,
                          decipher_only=False),
            critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.OCSP_SIGNING]),
            critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public),
                       critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public),
            critical=False)
    )
    return _sign(builder, issuer_key)


def _sign(builder: x509.CertificateBuilder, key: keys.KeyPair) -> "Certificate":
    cert = builder.sign(key.private, _hash_for(key))
    return Certificate(cert)


@dataclass(frozen=True)
class Certificate:
    """A signed X.509 certificate with PEM/DER helpers."""
    cert: x509.Certificate

    @property
    def subject(self) -> x509.Name:
        return self.cert.subject

    @property
    def issuer(self) -> x509.Name:
        return self.cert.issuer

    def der(self) -> bytes:
        from cryptography.hazmat.primitives.serialization import Encoding
        return self.cert.public_bytes(Encoding.DER)

    def pem(self) -> bytes:
        from cryptography.hazmat.primitives.serialization import Encoding
        return self.cert.public_bytes(Encoding.PEM)

    def subject_key_identifier(self) -> bytes:
        """SHA-1 SubjectKeyIdentifier of this certificate (A/360 5.2.2.2)."""
        from cryptography.hazmat.primitives import hashes as _h
        return x509.SubjectKeyIdentifier.from_public_key(
            self.cert.public_key()).digest

    def bsids(self) -> Tuple[int, ...]:
        """The id-atsc-sdattr-bsid set, when the extension is present."""
        from .sda import decode_bsid_sda
        try:
            ext = self.cert.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_DIRECTORY_ATTRIBUTES)
        except x509.ExtensionNotFound:
            return ()
        return decode_bsid_sda(ext.value.value)

    def signaling_eku(self) -> bool:
        """True when the certificate carries id-atsc-kp-signalingSigning."""
        try:
            eku = self.cert.extensions.get_extension_for_class(
                x509.ExtendedKeyUsage).value
        except x509.ExtensionNotFound:
            return False
        return x509.ObjectIdentifier(ID_ATSC_KP_SIGNALING_SIGNING) in eku

    def to_pem_file(self, path: str) -> None:
        with open(path, "wb") as fh:
            fh.write(self.pem())


def load_der(data: bytes) -> Certificate:
    return Certificate(x509.load_der_x509_certificate(data))


def load_pem(data: bytes) -> Certificate:
    return Certificate(x509.load_pem_x509_certificate(data))
