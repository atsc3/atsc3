"""Pluggable signing backends: the key-custody seam.

Every operation that signs — X.509 issuance, CMS ``SignedData`` for LLS/CDT,
and OCSP responses — goes through a :class:`Signer` so the private key can live
anywhere.  Two backends ship here:

* :class:`LocalSigner` wraps an in-process ``cryptography`` private key.  It is
  the default and the only backend the offline tests need.
* :class:`KmsSigner` signs with an AWS KMS asymmetric key (``kms:Sign``); the
  key material never leaves KMS.  ``boto3`` is imported lazily and is an
  optional extra, so this package stays dependency-light and is not locked to
  AWS.

The hash for each curve is fixed by A/360 5.2.2.1 item 4 (P-256/SHA-256,
P-384/SHA-384, P-521/SHA-512), which is also what KMS requires: an
``ECC_NIST_P384`` key only accepts ``ECDSA_SHA_384``.  KMS has no P-521.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Protocol, runtime_checkable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

#: A/360 5.2.2.1 item 4: the permitted ECDSA curve/digest pairs.
CURVE_HASH = {
    "secp256r1": "sha256",
    "secp384r1": "sha384",
    "secp521r1": "sha512",
}

#: Curve name -> ``cryptography`` hash class.
HASH_ALGORITHMS = {
    "sha256": hashes.SHA256,
    "sha384": hashes.SHA384,
    "sha512": hashes.SHA512,
}

#: Curve name -> ``cryptography`` curve class.
EC_CURVES = {
    "secp256r1": ec.SECP256R1,
    "secp384r1": ec.SECP384R1,
    "secp521r1": ec.SECP521R1,
}

#: Digest name -> the KMS ``SigningAlgorithm`` for that digest.
KMS_SIGNING_ALGORITHMS = {
    "sha256": "ECDSA_SHA_256",
    "sha384": "ECDSA_SHA_384",
    "sha512": "ECDSA_SHA_512",
}

#: KMS key spec -> curve name.
KMS_CURVES = {
    "ECC_NIST_P256": "secp256r1",
    "ECC_NIST_P384": "secp384r1",
    "ECC_NIST_P521": "secp521r1",
}


@runtime_checkable
class Signer(Protocol):
    """A key that can sign, without exposing private key material."""

    @property
    def curve(self) -> str:
        """The A/360 curve name (``secp256r1``/``secp384r1``/``secp521r1``)."""
        ...

    @property
    def public(self) -> ec.EllipticCurvePublicKey:
        """The matching public key, for the certificate being issued."""
        ...

    def sign(self, data: bytes, hash_name: str) -> bytes:
        """Return the ECDSA signature over ``data`` using ``hash_name``."""
        ...


def hash_for_curve(curve: str) -> str:
    """The A/360 5.2.2.1 permitted digest name for ``curve``."""
    try:
        return CURVE_HASH[curve]
    except KeyError:
        raise ValueError(f"curve {curve!r} has no A/360 permitted digest") from None


class LocalSigner:
    """A signer backed by an in-process ``cryptography`` private key."""

    def __init__(self, private: ec.EllipticCurvePrivateKey) -> None:
        self._private = private

    @property
    def private(self) -> ec.EllipticCurvePrivateKey:
        return self._private

    @property
    def curve(self) -> str:
        return self._private.curve.name

    @property
    def public(self) -> ec.EllipticCurvePublicKey:
        return self._private.public_key()

    def sign(self, data: bytes, hash_name: str) -> bytes:
        return self._private.sign(data, ec.ECDSA(HASH_ALGORITHMS[hash_name]()))

    def __repr__(self) -> str:
        return f"LocalSigner(curve={self.curve!r})"


class KmsSigner:
    """A signer whose private key never leaves AWS KMS.

    ``client`` is any object exposing the two boto3 KMS calls used here —
    ``get_public_key(KeyId=...)`` and ``sign(**kwargs)``.  Injecting it keeps
    this package free of a hard ``boto3`` dependency and makes the backend
    testable without AWS; :meth:`for_key` builds the real boto3 client.
    """

    def __init__(self, key_id: str, client, curve: Optional[str] = None) -> None:
        self._key_id = key_id
        self._client = client
        self._curve = curve
        self._public: Optional[ec.EllipticCurvePublicKey] = None

    @classmethod
    def for_key(cls, key_id: str, *, region: Optional[str] = None,
                client=None, curve: Optional[str] = None) -> "KmsSigner":
        """Build a signer for ``key_id``, creating a boto3 client when needed."""
        if client is None:
            import boto3
            client = boto3.client("kms", region_name=region)
        return cls(key_id, client, curve=curve)

    @property
    def key_id(self) -> str:
        return self._key_id

    def _load_public(self) -> ec.EllipticCurvePublicKey:
        if self._public is None:
            response = self._client.get_public_key(KeyId=self._key_id)
            self._public = serialization.load_der_public_key(response["PublicKey"])
            spec = response.get("KeySpec") or response.get("CustomerMasterKeySpec")
            if self._curve is None and spec in KMS_CURVES:
                self._curve = KMS_CURVES[spec]
        return self._public

    @property
    def public(self) -> ec.EllipticCurvePublicKey:
        return self._load_public()

    @property
    def curve(self) -> str:
        if self._curve is None:
            self._load_public()
        return self._curve

    def sign(self, data: bytes, hash_name: str) -> bytes:
        expected = hash_for_curve(self.curve)
        if expected != hash_name:
            raise ValueError(
                f"KMS {self.curve} key must sign with {expected}, not {hash_name}")
        digest = hashlib.new(hash_name, data).digest()
        response = self._client.sign(
            KeyId=self._key_id,
            Message=digest,
            MessageType="DIGEST",
            SigningAlgorithm=KMS_SIGNING_ALGORITHMS[hash_name],
        )
        return response["Signature"]

    def __repr__(self) -> str:
        return f"KmsSigner(key_id={self._key_id!r}, curve={self._curve!r})"


class EnvelopeSigner:
    """A signer whose private key is stored encrypted under a KMS CMK.

    This is the low-cost leaf-key backend: the private key is generated as a
    KMS data key pair (or by the caller), stored as ciphertext, and decrypted in
    process only while signing.  The tenant CA key stays a non-exportable KMS
    asymmetric key; only leaf keys use this backend.
    """

    def __init__(self, ciphertext: bytes, wrapping_key_id: str, client,
                 public_key_der: bytes, *, curve: Optional[str] = None,
                 encryption_context: Optional[dict] = None) -> None:
        self._ciphertext = ciphertext
        self._wrapping_key_id = wrapping_key_id
        self._client = client
        self._public = serialization.load_der_public_key(public_key_der)
        if not isinstance(self._public, ec.EllipticCurvePublicKey):
            raise ValueError("wrapped leaf keys must be EC keys")
        self._curve = curve or self._public.curve.name
        self._encryption_context = encryption_context

    @classmethod
    def for_wrapped(cls, ciphertext: bytes, wrapping_key_id: str,
                    public_key_der: bytes, *, region: Optional[str] = None,
                    client=None, curve: Optional[str] = None,
                    encryption_context: Optional[dict] = None) -> "EnvelopeSigner":
        if client is None:
            import boto3
            client = boto3.client("kms", region_name=region)
        return cls(ciphertext, wrapping_key_id, client, public_key_der,
                   curve=curve, encryption_context=encryption_context)

    @property
    def wrapping_key_id(self) -> str:
        return self._wrapping_key_id

    @property
    def public(self) -> ec.EllipticCurvePublicKey:
        return self._public

    @property
    def curve(self) -> str:
        return self._curve

    def sign(self, data: bytes, hash_name: str) -> bytes:
        expected = hash_for_curve(self.curve)
        if expected != hash_name:
            raise ValueError(
                f"wrapped {self.curve} key must sign with {expected}, not {hash_name}")
        kwargs = {"KeyId": self._wrapping_key_id,
                  "CiphertextBlob": self._ciphertext}
        if self._encryption_context:
            kwargs["EncryptionContext"] = self._encryption_context
        response = self._client.decrypt(**kwargs)
        private = serialization.load_der_private_key(
            response["Plaintext"], password=None)
        if not isinstance(private, ec.EllipticCurvePrivateKey):
            raise ValueError("wrapped key is not an ECDSA private key")
        if private.curve.name != self.curve:
            raise ValueError("wrapped key curve does not match the certificate key")
        signature = private.sign(data, ec.ECDSA(HASH_ALGORITHMS[hash_name]()))
        del private
        return signature

    def __repr__(self) -> str:
        return f"EnvelopeSigner(wrapping_key_id={self._wrapping_key_id!r}, curve={self._curve!r})"


def as_signer(obj) -> Signer:
    """Coerce a :class:`~openatsc3_pki.keys.KeyPair` or :class:`Signer`.

    Everything that signs calls this, so a caller may pass either a local
    :class:`~openatsc3_pki.keys.KeyPair` (the existing API) or any backend that
    implements :class:`Signer`.
    """
    if hasattr(obj, "sign") and callable(obj.sign):
        if not hasattr(obj, "public") or not hasattr(obj, "curve"):
            raise TypeError(f"{type(obj).__name__} is not a complete Signer")
        return obj
    private = getattr(obj, "private", None)
    if private is not None:
        return LocalSigner(private)
    raise TypeError(f"expected a Signer or keys.KeyPair, got {type(obj).__name__}")
