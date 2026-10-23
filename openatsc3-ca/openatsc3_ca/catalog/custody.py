"""Key-custody backends for tenant leaf keys.

The tenant CA key is always a non-exportable KMS asymmetric key in production.
Leaf keys have two tiers:

* ``premium`` - one non-exportable KMS asymmetric key per leaf (``kms:Sign``).
* ``standard`` - an EC data key pair wrapped under a per-tenant symmetric KMS
  CMK; the ciphertext is stored on the certificate row and decrypted in the
  signing process only.

``local`` is the test/development fallback and is selected when
``OPENATSC3_CA_KEY_BACKEND`` is not ``aws``.
"""

from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass

from django.conf import settings

from openatsc3_pki.signer import EnvelopeSigner, KmsSigner

from .constants import KeyCustody

try:
    from botocore.exceptions import ClientError as _ClientError
except ImportError:
    _ClientError = None


class KeyCustodyError(ValueError):
    """A key could not be created or selected for the requested tier."""


class _KeyNotFound(Exception):
    pass


class _AliasExists(Exception):
    pass


_CUSTODY_ERRORS = (_KeyNotFound, _AliasExists) + (
    (_ClientError,) if _ClientError else ())


#: ``cryptography`` curve -> KMS key spec.
_KMS_SPECS = {
    "secp256r1": "ECC_NIST_P256",
    "secp384r1": "ECC_NIST_P384",
    "secp521r1": "ECC_NIST_P521",
}

#: Default alias prefix; keep in sync with the Terraform KMS scope.
ALIAS_PREFIX = "alias/openatsc3-ca"


@dataclass(frozen=True)
class KeyMaterial:
    """The key references and signer for one issued leaf."""

    custody: str
    signer: object
    kms_key_id: str = ""
    wrapped_key: str = ""
    wrapping_key_id: str = ""
    wrapped_public_key: str = ""


def backend() -> str:
    """The configured backend: ``aws`` or ``local``."""
    return getattr(settings, "OPENATSC3_CA_KEY_BACKEND", "local").lower()


def enabled() -> bool:
    return backend() == "aws"


def kms_client():
    """The injected test client, or a real boto3 KMS client."""
    client = getattr(settings, "OPENATSC3_CA_KMS_CLIENT", None)
    if client is not None:
        return client
    if not enabled():
        raise KeyCustodyError("the AWS key backend is not enabled")
    import boto3
    return boto3.client(
        "kms", region_name=getattr(settings, "OPENATSC3_CA_KMS_REGION", None))


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "key"


def _alias(*parts: str) -> str:
    prefix = getattr(settings, "OPENATSC3_CA_KMS_ALIAS_PREFIX", ALIAS_PREFIX)
    return "/".join([prefix.rstrip("/")] + [_safe(p) for p in parts])


def _tags(tenant=None, profile: str = "") -> list:
    tags = [{"TagKey": "Project", "TagValue": "openatsc3-ca"}]
    if tenant is not None:
        tags.append({"TagKey": "Tenant", "TagValue": tenant.slug})
    if profile:
        tags.append({"TagKey": "Profile", "TagValue": profile})
    return tags


def _code(exc) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return response.get("Error", {}).get("Code", "")
    return ""


def _describe(client, key_id: str):
    try:
        return client.describe_key(KeyId=key_id)
    except _CUSTODY_ERRORS as exc:
        if isinstance(exc, _KeyNotFound) or _code(exc) == "NotFoundException":
            raise _KeyNotFound(key_id) from exc
        raise


def _create_alias(client, alias: str, key_id: str) -> bool:
    try:
        client.create_alias(AliasName=alias, TargetKeyId=key_id)
        return True
    except _CUSTODY_ERRORS as exc:
        if isinstance(exc, _AliasExists) or _code(exc) == "AlreadyExistsException":
            return False
        raise


def _new_asymmetric_key(client, *, description: str, curve: str,
                        tenant=None, profile: str = "") -> str:
    spec = _KMS_SPECS.get(curve)
    if spec is None:
        raise KeyCustodyError(f"unsupported curve {curve!r}")
    response = client.create_key(
        Description=description, KeyUsage="SIGN_VERIFY", KeySpec=spec,
        Tags=_tags(tenant, profile))
    return response["KeyMetadata"]["Arn"]


def _new_symmetric_key(client, *, description: str, tenant=None) -> str:
    response = client.create_key(
        Description=description, KeyUsage="ENCRYPT_DECRYPT",
        KeySpec="SYMMETRIC_DEFAULT", Tags=_tags(tenant, "wrapping"))
    return response["KeyMetadata"]["Arn"]


def ensure_tenant_wrapping_key(tenant) -> str:
    """Return the tenant's symmetric wrapping CMK, creating it once."""
    if tenant.wrapping_key_id:
        return tenant.wrapping_key_id
    client = kms_client()
    alias = _alias(tenant.slug, "wrap")
    try:
        metadata = _describe(client, alias)["KeyMetadata"]
        arn = metadata["Arn"]
    except _KeyNotFound:
        arn = _new_symmetric_key(
            client, description=f"openatsc3-ca {tenant.slug} leaf wrapping key",
            tenant=tenant)
        key_id = arn.rsplit("/", 1)[-1]
        if not _create_alias(client, alias, key_id):
            arn = _describe(client, alias)["KeyMetadata"]["Arn"]
    tenant.wrapping_key_id = arn
    tenant.save(update_fields=["wrapping_key_id"])
    return arn


def create_leaf_key(tenant, *, profile: str, label: str,
                    curve: str = "secp256r1") -> KeyMaterial:
    """Create a premium KMS key or a standard wrapped data key pair."""
    if not enabled():
        raise KeyCustodyError("the AWS key backend is not enabled")
    if tenant is None:
        raise KeyCustodyError("leaf keys require a tenant")
    if tenant.custody == KeyCustody.PREMIUM:
        return _premium_leaf(tenant, profile=profile, label=label, curve=curve)
    return _standard_leaf(tenant, profile=profile, label=label, curve=curve)


def _premium_leaf(tenant, *, profile: str, label: str,
                  curve: str) -> KeyMaterial:
    client = kms_client()
    arn = _new_asymmetric_key(
        client, description=f"openatsc3-ca {tenant.slug} {profile} {label}",
        curve=curve, tenant=tenant, profile=profile)
    key_id = arn.rsplit("/", 1)[-1]
    _create_alias(
        client,
        _alias(tenant.slug, "leaves", profile, label, uuid.uuid4().hex[:8]),
        key_id)
    return KeyMaterial(
        custody=KeyCustody.PREMIUM,
        signer=KmsSigner(arn, client, curve=curve),
        kms_key_id=arn)


def _standard_leaf(tenant, *, profile: str, label: str,
                   curve: str) -> KeyMaterial:
    client = kms_client()
    wrapping_key_id = ensure_tenant_wrapping_key(tenant)
    spec = _KMS_SPECS.get(curve)
    if spec is None:
        raise KeyCustodyError(f"unsupported curve {curve!r}")
    response = client.generate_data_key_pair(
        KeyId=wrapping_key_id, KeyPairSpec=spec)
    ciphertext = response["PrivateKeyCiphertextBlob"]
    public_der = response["PublicKey"]
    wrapped_public = base64.b64encode(public_der).decode("ascii")
    return KeyMaterial(
        custody=KeyCustody.STANDARD,
        signer=EnvelopeSigner(
            ciphertext, wrapping_key_id, client, public_der, curve=curve),
        wrapped_key=base64.b64encode(ciphertext).decode("ascii"),
        wrapping_key_id=wrapping_key_id,
        wrapped_public_key=wrapped_public)


def signer_for_certificate(cert):
    """The signer for a ``Certificate`` row, or ``None`` for a local key file."""
    if cert.kms_key_id:
        client = getattr(settings, "OPENATSC3_CA_KMS_CLIENT", None)
        if client is not None:
            return KmsSigner(cert.kms_key_id, client)
        return KmsSigner.for_key(cert.kms_key_id)
    if cert.wrapped_key:
        if not cert.wrapping_key_id:
            raise KeyCustodyError("wrapped key has no wrapping CMK")
        ciphertext = base64.b64decode(cert.wrapped_key)
        if cert.wrapped_public_key:
            public_der = base64.b64decode(cert.wrapped_public_key)
        else:
            raise KeyCustodyError("wrapped key has no stored public key")
        return EnvelopeSigner(
            ciphertext, cert.wrapping_key_id, kms_client(), public_der)
    return None
