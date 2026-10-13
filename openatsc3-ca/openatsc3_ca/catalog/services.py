"""Business logic: the DB ledger calls into ``openatsc3_pki`` here.

No cryptography is reimplemented — these functions orchestrate the existing
``openatsc3_pki`` primitives (``ca``, ``x509``, ``ocsp``) and record the result
in the catalog.  Keys are written 0600 on disk by the library; we store the path
and its SHA-256 so integrity can be checked without ever putting key bytes in
the database.

The PEM tree under ``OPENATSC3_CA_BASE_DIR`` is a materialized view.  ``issue_*``
functions write it (the library does, for compatibility with the receiver and
signer); ``export`` rebuilds it from the database.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from openatsc3_pki import ca as pki_ca
from openatsc3_pki import cdt as pki_cdt
from openatsc3_pki import ceremony as pki_ceremony
from openatsc3_pki import crl as pki_crl
from openatsc3_pki import keys as pki_keys
from openatsc3_pki import ocsp as pki_ocsp
from openatsc3_pki import x509 as pki_x509
from openatsc3_pki.x509 import Certificate as X509Certificate
from openatsc3_pki.x509 import SubjectInfo
from cryptography import x509 as _x509
from cryptography.x509.oid import ExtensionOID, NameOID

from .constants import (
    OCSP_REFRESH_DEFAULT,
    CertStatus,
    OrderStatus,
    Profile,
    RolloverState,
)
from .delivery import default_deliverer
from .models import (
    AuditEvent,
    Broadcaster,
    Certificate,
    OcspResponder,
    OcspResponse,
    Order,
    PublishedSignaling,
    RolloverPlan,
    Tenant,
)
from .payments import default_gateway


# --- paths -----------------------------------------------------------------

def base_dir() -> Path:
    return Path(settings.OPENATSC3_CA_BASE_DIR)


def _certs_dir() -> Path:
    return base_dir() / "certs"


def _ocsp_dir() -> Path:
    return base_dir() / "ocsp"


def _write_bytes(path: Path, data: bytes, mode: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, mode if mode is not None else 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _safe(name: str) -> str:
    """Filesystem-safe path element for a broadcaster name."""
    return name.replace("/", "_").replace("..", "_")


# --- audit -----------------------------------------------------------------

def audit(action: str, *, actor: str = "system", target=None,
          detail: Optional[dict] = None) -> AuditEvent:
    target_type = target.__class__.__name__ if target is not None else ""
    target_id = str(getattr(target, "pk", "") or "")
    return AuditEvent.objects.create(
        actor=actor, action=action, target_type=target_type,
        target_id=target_id, detail=detail or {})


# --- certificate introspection --------------------------------------------

def _name_attr(name: _x509.Name, oid) -> str:
    values = name.get_attributes_for_oid(oid)
    return values[0].value if values else ""


def _aki_hex(cert: X509Certificate) -> str:
    try:
        aki = cert.cert.extensions.get_extension_for_oid(
            ExtensionOID.AUTHORITY_KEY_IDENTIFIER).value
    except _x509.ExtensionNotFound:
        return ""
    return aki.key_identifier.hex() if aki.key_identifier else ""


def _eku_oids(cert: X509Certificate) -> List[str]:
    try:
        eku = cert.cert.extensions.get_extension_for_class(
            _x509.ExtendedKeyUsage).value
    except _x509.ExtensionNotFound:
        return []
    return [oid.dotted_string for oid in eku]


@dataclass
class CertRecordResult:
    certificate: Certificate
    created: bool


def record_certificate(cert: X509Certificate, profile: str, *,
                       key_path: str = "", kms_key_id: str = "",
                       broadcaster: Optional[Broadcaster] = None,
                       tenant: Optional[Tenant] = None,
                       parent: Optional[Certificate] = None,
                       der_file: Optional[str] = None) -> CertRecordResult:
    """Persist an issued certificate's metadata into the ledger.

    ``der_file`` defaults to ``<base>/certs/<serial>.der`` (written here).
    ``parent`` defaults to the certificate whose SKI matches this AKI, so
    imported chains link up without the caller passing parents explicitly.
    A key is recorded either as a 0600 file (``key_path`` + its SHA-256) or as a
    KMS key id (``kms_key_id``); a KMS key never touches the filesystem.
    """
    serial = str(cert.cert.serial_number)
    if der_file is None:
        der_path = _certs_dir() / f"{serial}.der"
        _write_bytes(der_path, cert.der())
        der_file = str(der_path)

    if parent is None:
        aki = _aki_hex(cert)
        if aki:
            parent = Certificate.objects.filter(ski=aki).first()

    subject = cert.cert.subject
    defaults = {
        "subject_dn": subject.rfc4514_string(),
        "common_name": _name_attr(subject, NameOID.COMMON_NAME),
        "organization": _name_attr(subject, NameOID.ORGANIZATION_NAME),
        "organizational_unit": _name_attr(subject, NameOID.ORGANIZATIONAL_UNIT_NAME),
        "profile": profile,
        "not_before": cert.cert.not_valid_before_utc,
        "not_after": cert.cert.not_valid_after_utc,
        "der_file": der_file,
        "ski": cert.subject_key_identifier().hex(),
        "aki": _aki_hex(cert),
        "bsids": list(cert.bsids()),
        "eku": _eku_oids(cert),
        "parent": parent,
        "key_path": key_path,
        "key_sha256": sha256_file(key_path) if key_path else "",
        "kms_key_id": kms_key_id,
        "broadcaster": broadcaster,
        "tenant": tenant,
    }
    obj, created = Certificate.objects.update_or_create(
        serial=serial, defaults=defaults)
    return CertRecordResult(obj, created)


# --- issuance (writes keys + PEM tree via the library) ---------------------

def _authority() -> pki_ca.CertificateAuthority:
    return pki_ca.CertificateAuthority(str(base_dir()))


def root_is_offline() -> bool:
    """True when the root key is not available online (air-gapped quorum).

    Set ``OPENATSC3_CA_ROOT_OFFLINE`` (or run an air-gapped ceremony); online
    issuance that needs the root then fails with a pointer to the ceremony
    instead of reading a local root key.
    """
    return bool(getattr(settings, "OPENATSC3_CA_ROOT_OFFLINE", False))


def _signer_for(key=None, kms_key_id: str = ""):
    """An external signing backend: an explicit signer, or a KMS key id.

    ``None`` means "use a local key on disk".  This is the single place the CA
    app turns a stored key reference into a :class:`~openatsc3_pki.signer.Signer`
    for anything other than the local test path.
    """
    if key is not None:
        return key
    if kms_key_id:
        from openatsc3_pki.signer import KmsSigner
        return KmsSigner.for_key(kms_key_id)
    return None


def _tenant_ca_cert(tenant: Tenant) -> Optional[Certificate]:
    return (Certificate.objects
            .filter(tenant=tenant, profile=Profile.TENANT_CA,
                    status=CertStatus.GOOD)
            .order_by("-created_at").first())


def tenant_ca(tenant: Tenant) -> Certificate:
    """The tenant's current intermediate CA row, regardless of status.

    Used to rebuild a customer's bundle (which must keep referencing the CA
    even after it is revoked) and to read the CA back for verification.
    """
    ca = (Certificate.objects
          .filter(tenant=tenant, profile=Profile.TENANT_CA)
          .order_by("-created_at").first())
    if ca is None:
        raise ValueError(f"tenant {tenant.slug!r} has no tenant CA")
    return ca


def _tenant_ca_signer(tenant: Tenant):
    """The tenant CA's signer, or ``None`` to load its on-disk key."""
    ca = _tenant_ca_cert(tenant)
    if ca is None:
        raise ValueError(f"tenant {tenant.slug!r} has no good tenant CA")
    if ca.kms_key_id:
        from openatsc3_pki.signer import KmsSigner
        return KmsSigner.for_key(ca.kms_key_id)
    return None


def _cert_signer(cert: Certificate):
    """A certificate's signing key: a KMS backend or the on-disk key file."""
    if cert.kms_key_id:
        from openatsc3_pki.signer import KmsSigner
        return KmsSigner.for_key(cert.kms_key_id)
    return pki_keys.load_private(cert.key_path)


def _responder_signer(responder: OcspResponder):
    """The OCSP responder's signing key: a KMS backend or the on-disk key."""
    return _cert_signer(responder.certificate)


def issue_root(*, common_name: str = "OpenATSC3 Root CA",
               organization: str = "OpenATSC3", days: int = 7300,
               actor: str = "system") -> Certificate:
    authority = _authority()
    cert = authority.init_root(common_name=common_name, organization=organization,
                               days=days)
    with transaction.atomic():
        result = record_certificate(cert, Profile.ROOT,
                                    key_path=authority.root_key_path)
        audit("issue-root", actor=actor, target=result.certificate,
              detail={"cn": common_name})
    return result.certificate


def issue_issuing(*, common_name: str = "OpenATSC3 Issuing CA", days: int = 3650,
                  actor: str = "system") -> Certificate:
    authority = _authority()
    cert = authority.issue_issuing(common_name=common_name, days=days)
    with transaction.atomic():
        result = record_certificate(cert, Profile.ISSUING_CA,
                                    key_path=authority.issuing_key_path)
        audit("issue-issuing-ca", actor=actor, target=result.certificate,
              detail={"cn": common_name})
    return result.certificate


def issue_tenant_ca(tenant: Tenant, *, common_name: Optional[str] = None,
                    days: int = 3650, kms_key_id: str = "", key=None,
                    actor: str = "system") -> Certificate:
    """Issue a per-tenant intermediate CA signed by the offline root.

    Revoking this certificate disables one customer (the tenant's leaves and
    the tenant itself), leaving the root and other tenants untouched.
    ``key``/``kms_key_id`` inject an external signer (KMS); otherwise a local
    P-384 key is generated and written under ``tenants/<slug>/``.
    """
    if root_is_offline():
        raise ValueError(
            "the root key is offline; create this tenant CA by ceremony: "
            "`manage.py request-tenant-ca` -> `openatsc3-pki sign-request` -> "
            "`manage.py import-signed --profile tenant-ca`")
    authority = _authority()
    signer = _signer_for(key, kms_key_id)
    cert = authority.issue_tenant_ca(tenant.slug, common_name=common_name,
                                     days=days, key=signer)
    key_path = "" if signer is not None else authority.tenant_key_path(tenant.slug)
    with transaction.atomic():
        result = record_certificate(cert, Profile.TENANT_CA, tenant=tenant,
                                    key_path=key_path, kms_key_id=kms_key_id)
        audit("issue-tenant-ca", actor=actor, target=result.certificate,
              detail={"tenant": tenant.slug, "cn": common_name})
    return result.certificate


def _issue_leaf(profile: str, action: str, label: str, name: str,
                bsids: Iterable[int], *,
                tenant: Optional[Tenant] = None,
                common_name: Optional[str] = None, days: int = 365,
                organization: Optional[str] = None,
                kms_key_id: str = "", key=None,
                actor: str = "system") -> Certificate:
    """Issue a signaling-signer leaf for ``name`` under ``profile``.

    Shared by :func:`issue_broadcaster` (CurrentCert/NextCert) and
    :func:`issue_cdt_signer` (the distinct CertificationData signing key,
    A/360 5.2.2.2).  ``label`` names the leaf's files, so the two profiles do
    not share a key path.  ``tenant`` selects the issuing CA; ``key``/
    ``kms_key_id`` inject an external signer (KMS), writing no key file when
    external.
    """
    bsids = tuple(int(b) for b in bsids)
    broadcaster, _ = Broadcaster.objects.get_or_create(
        name=name, tenant=tenant,
        defaults={"organization": organization or name, "bsids": list(bsids)})
    if list(bsids) and list(broadcaster.bsids) != list(bsids):
        broadcaster.bsids = list(bsids)
        broadcaster.save(update_fields=["bsids"])
    authority = _authority()
    tenant_slug = tenant.slug if tenant is not None else None
    signer = _signer_for(key, kms_key_id)
    ca_key = _tenant_ca_signer(tenant) if tenant is not None else None
    cert = authority.issue_broadcaster(name, bsids, common_name=common_name,
                                       days=days, tenant=tenant_slug,
                                       key=signer, ca_key=ca_key, label=label)
    key_path = ("" if signer is not None else
                os.path.join(authority.broadcaster_dir(name, tenant_slug),
                             f"{label}.key.pem"))
    with transaction.atomic():
        result = record_certificate(cert, profile, key_path=key_path,
                                    kms_key_id=kms_key_id,
                                    broadcaster=broadcaster, tenant=tenant)
        audit(action, actor=actor, target=result.certificate,
              detail={"name": name, "bsids": list(bsids), "tenant": tenant_slug})
    return result.certificate


def issue_broadcaster(name: str, bsids: Iterable[int], *,
                      tenant: Optional[Tenant] = None,
                      common_name: Optional[str] = None, days: int = 365,
                      organization: Optional[str] = None,
                      kms_key_id: str = "", key=None,
                      actor: str = "system") -> Certificate:
    """Issue a broadcast signaling-signer (CurrentCert/NextCert) leaf."""
    return _issue_leaf(Profile.SIGNALING_SIGNER, "issue-broadcaster", "signing",
                       name, bsids, tenant=tenant, common_name=common_name,
                       days=days, organization=organization,
                       kms_key_id=kms_key_id, key=key, actor=actor)


def issue_cdt_signer(name: str, bsids: Iterable[int], *,
                     tenant: Optional[Tenant] = None,
                     common_name: Optional[str] = None, days: int = 365,
                     organization: Optional[str] = None,
                     kms_key_id: str = "", key=None,
                     actor: str = "system") -> Certificate:
    """Issue the distinct CertificationData (CDT `0x06`) signing key.

    A/360 5.2.2.2 requires the CDT signing key to differ from the
    CurrentCert/NextCert signaling keys; A/360 5.3.1.6 is the profile used.
    """
    return _issue_leaf(Profile.CDT_SIGNER, "issue-cdt-signer", "cdt-signer",
                       name, bsids, tenant=tenant, common_name=common_name,
                       days=days, organization=organization,
                       kms_key_id=kms_key_id, key=key, actor=actor)


def issue_ocsp_responder(*, tenant: Optional[Tenant] = None,
                         common_name: str = "OpenATSC3 OCSP Responder",
                         days: int = 365, kms_key_id: str = "", key=None,
                         actor: str = "system") -> OcspResponder:
    authority = _authority()
    if tenant is None:
        issuing = authority.load_issuing()
        issuing_key = authority.load_issuing_key()
        responder_dir = base_dir()
    else:
        issuing = authority.load_tenant(tenant.slug)
        issuing_key = (_tenant_ca_signer(tenant)
                       or authority.load_tenant_key(tenant.slug))
        responder_dir = Path(authority.tenant_dir(tenant.slug))
    signer = _signer_for(key, kms_key_id)
    key_path = responder_dir / "ocsp-responder.key.pem"
    if signer is None:
        signer = pki_keys.generate(pki_keys.SIGNER_CURVE)
        pki_keys.write_private(str(key_path), signer)
        key_path_str = str(key_path)
    else:
        key_path_str = ""
    subject = SubjectInfo(
        common_name=common_name,
        organizational_unit="ATSC OCSP Responder",
        organization=_name_attr(issuing.cert.subject, NameOID.ORGANIZATION_NAME),
    )
    cert = pki_x509.issue_ocsp_responder(subject, signer, issuing, issuing_key,
                                         days=days)
    with transaction.atomic():
        result = record_certificate(cert, Profile.OCSP_RESPONDER, tenant=tenant,
                                    key_path=key_path_str, kms_key_id=kms_key_id)
        responder = OcspResponder.objects.create(
            common_name=common_name, certificate=result.certificate,
            key_path=key_path_str,
            key_sha256=sha256_file(key_path_str) if key_path_str else "",
            active=True)
        audit("issue-ocsp-responder", actor=actor, target=responder,
              detail={"cn": common_name,
                      "tenant": tenant.slug if tenant is not None else None})
    return responder


def issue_root_responder(*, common_name: str = "OpenATSC3 Root OCSP Responder",
                         days: int = 365, kms_key_id: str = "", key=None,
                         actor: str = "system") -> OcspResponder:
    """Issue the root-delegated revocation responder (A/360 5.3.1.7).

    The offline root signs this responder's certificate (a ceremony in
    production); its online key is the only authority that may sign OCSP
    responses for the per-tenant intermediate CAs, whose issuer is the root.
    It is kept out of the cert-minting path, so its compromise can suppress or
    deny revocation but cannot mint certificates.
    """
    if root_is_offline():
        raise ValueError(
            "the root key is offline; create this responder by ceremony: "
            "`manage.py request-root-responder` -> `openatsc3-pki sign-request` -> "
            "`manage.py import-signed --profile ocsp-responder --responder`")
    authority = _authority()
    root = authority.load_root()
    root_key = authority.load_root_key()
    signer = _signer_for(key, kms_key_id)
    responder_dir = base_dir() / "root"
    key_path = responder_dir / "ocsp-responder.key.pem"
    if signer is None:
        signer = pki_keys.generate(pki_keys.SIGNER_CURVE)
        pki_keys.write_private(str(key_path), signer)
        key_path_str = str(key_path)
    else:
        key_path_str = ""
    subject = SubjectInfo(
        common_name=common_name,
        organizational_unit="ATSC OCSP Responder",
        organization=_name_attr(root.cert.subject, NameOID.ORGANIZATION_NAME),
    )
    cert = pki_x509.issue_ocsp_responder(subject, signer, root, root_key,
                                         days=days)
    with transaction.atomic():
        result = record_certificate(cert, Profile.OCSP_RESPONDER,
                                    key_path=key_path_str, kms_key_id=kms_key_id)
        responder = OcspResponder.objects.create(
            common_name=common_name, certificate=result.certificate,
            key_path=key_path_str,
            key_sha256=sha256_file(key_path_str) if key_path_str else "",
            active=True)
        audit("issue-root-responder", actor=actor, target=responder,
              detail={"cn": common_name})
    return responder


# --- offline-root ceremony (request -> air-gapped sign -> import) ----------

def _local_key_path(profile: str, tenant: Optional[Tenant]) -> str:
    """The on-disk key path for a certificate profile ('' when KMS/unknown)."""
    authority = _authority()
    if profile == Profile.TENANT_CA and tenant is not None:
        return authority.tenant_key_path(tenant.slug)
    if profile == Profile.OCSP_RESPONDER:
        if tenant is not None:
            return str(Path(authority.tenant_dir(tenant.slug)) / "ocsp-responder.key.pem")
        return str(base_dir() / "root" / "ocsp-responder.key.pem")
    if profile in (Profile.SIGNALING_SIGNER, Profile.CDT_SIGNER):
        # A leaf's key path depends on the caller; the online side records it.
        return ""
    return ""


def _local_cert_path(profile: str, tenant: Optional[Tenant]) -> str:
    """The conventional on-disk cert path for a profile ('' when not applicable)."""
    authority = _authority()
    if profile == Profile.ROOT:
        return str(base_dir() / "root" / "root.cert.pem")
    if profile == Profile.TENANT_CA and tenant is not None:
        return authority.tenant_cert_path(tenant.slug)
    if profile == Profile.OCSP_RESPONDER:
        if tenant is not None:
            return str(Path(authority.tenant_dir(tenant.slug)) / "ocsp-responder.cert.pem")
        return str(base_dir() / "root" / "ocsp-responder.cert.pem")
    return ""


def request_tenant_ca(tenant: Tenant, *, common_name: Optional[str] = None,
                      days: int = 3650, kms_key_id: str = "", key=None,
                      actor: str = "system") -> bytes:
    """Build a tenant-CA ceremony request (placeholder DER) for the offline root.

    The tenant CA's key is created here if not supplied (a local file, or a KMS
    key id); only its public half goes into the request.  The root key is never
    needed online.
    """
    authority = _authority()
    root = authority.load_root()
    signer = _signer_for(key, kms_key_id)
    if signer is None:
        signer = pki_keys.generate(pki_keys.CA_CURVE)
        pki_keys.write_private(authority.tenant_key_path(tenant.slug), signer)
    public = pki_ceremony.PublicKey.from_signer(signer)
    subject = SubjectInfo(
        common_name=common_name or f"{tenant.slug} Intermediate CA",
        organizational_unit="ATSC Trust Authority",
        organization=_name_attr(root.cert.subject, NameOID.ORGANIZATION_NAME))
    request = pki_ceremony.ca_request(subject, public, root, days=days,
                                      curve=pki_keys.ROOT_CURVE)
    audit("request-tenant-ca", actor=actor, target=tenant,
          detail={"tenant": tenant.slug, "kms": bool(kms_key_id)})
    return request


def request_root_responder(*, common_name: str = "OpenATSC3 Root OCSP Responder",
                           days: int = 365, kms_key_id: str = "", key=None,
                           actor: str = "system") -> bytes:
    """Build a root-delegated OCSP-responder request for the offline root."""
    authority = _authority()
    root = authority.load_root()
    signer = _signer_for(key, kms_key_id)
    key_path = base_dir() / "root" / "ocsp-responder.key.pem"
    if signer is None:
        signer = pki_keys.generate(pki_keys.SIGNER_CURVE)
        pki_keys.write_private(str(key_path), signer)
    public = pki_ceremony.PublicKey.from_signer(signer)
    subject = SubjectInfo(
        common_name=common_name,
        organizational_unit="ATSC OCSP Responder",
        organization=_name_attr(root.cert.subject, NameOID.ORGANIZATION_NAME))
    request = pki_ceremony.responder_request(subject, public, root, days=days,
                                             curve=pki_keys.ROOT_CURVE)
    audit("request-root-responder", actor=actor,
          detail={"cn": common_name, "kms": bool(kms_key_id)})
    return request


def import_signed_certificate(data: bytes, profile: str, *,
                              tenant: Optional[Tenant] = None,
                              broadcaster: Optional[Broadcaster] = None,
                              kms_key_id: str = "", key_path: str = "",
                              make_responder: bool = False,
                              actor: str = "system") -> Certificate:
    """Record a certificate signed offline by the root into the ledger.

    ``data`` is the DER/PEM returned from the air-gapped signing step.  The
    ``parent`` is linked from the certificate's AuthorityKeyIdentifier, so the
    ledger chain (root -> tenant CA -> leaves) is reconstructed automatically.
    """
    cert = (pki_x509.load_pem(data) if data.lstrip().startswith(b"-----")
            else pki_x509.load_der(data))
    if not key_path:
        key_path = "" if kms_key_id else _local_key_path(profile, tenant)
    key_sha = sha256_file(key_path) if key_path and os.path.exists(key_path) else ""
    with transaction.atomic():
        result = record_certificate(cert, profile, tenant=tenant,
                                    broadcaster=broadcaster, key_path=key_path,
                                    kms_key_id=kms_key_id)
        out = _local_cert_path(profile, tenant)
        if out:
            _write_cert_pem(result.certificate, Path(out))
        if make_responder and not OcspResponder.objects.filter(
                certificate=result.certificate, active=True).exists():
            OcspResponder.objects.create(
                common_name=result.certificate.common_name,
                certificate=result.certificate, key_path=key_path,
                key_sha256=key_sha, active=True)
        audit("import-signed", actor=actor, target=result.certificate,
              detail={"profile": profile,
                      "tenant": tenant.slug if tenant is not None else None})
    return result.certificate


# --- signed bundle (publish) ----------------------------------------------

def _latest_leaf(broadcaster: Broadcaster, profile: str) -> Optional[Certificate]:
    return (Certificate.objects
            .filter(broadcaster=broadcaster, profile=profile,
                    status=CertStatus.GOOD)
            .order_by("-created_at").first())


def current_signer(broadcaster: Broadcaster) -> Certificate:
    """The broadcaster's CurrentCert: its latest good signaling signer."""
    cert = _latest_leaf(broadcaster, Profile.SIGNALING_SIGNER)
    if cert is None:
        raise ValueError(
            f"broadcaster {broadcaster.name!r} has no signaling signer")
    return cert


def cdt_signer(broadcaster: Broadcaster) -> Certificate:
    """The broadcaster's latest good CertificationData signer (A/360 5.2.2.2)."""
    cert = _latest_leaf(broadcaster, Profile.CDT_SIGNER)
    if cert is None:
        raise ValueError(
            f"broadcaster {broadcaster.name!r} has no CDT signer; "
            "run issue-cdt-signer")
    return cert


def active_rollover(broadcaster: Broadcaster) -> Optional[RolloverPlan]:
    """The broadcaster's open key-replacement window, if any."""
    return (RolloverPlan.objects
            .filter(broadcaster=broadcaster,
                    state__in=[RolloverState.PLANNED, RolloverState.ACTIVE])
            .order_by("-created_at").first())


def root_responder() -> OcspResponder:
    """The active root-delegated OCSP responder (see :func:`issue_root_responder`)."""
    responder = (OcspResponder.objects
                 .filter(active=True,
                         certificate__parent__profile=Profile.ROOT)
                 .order_by("-created_at").first())
    if responder is None:
        raise ValueError(
            "no active root OCSP responder; run issue-root-responder")
    return responder


def responder_for(cert: Certificate) -> OcspResponder:
    """An active responder authorized by ``cert``'s issuer (RFC 6960 4.2.2.2).

    A responder may sign OCSP only for certificates issued by the CA that issued
    the responder, so a valid responder has ``certificate.parent == cert.parent``.
    This is what makes CA revocation enforceable: a tenant CA's response can only
    come from a root-authorized responder, never from one the tenant controls.
    """
    qs = OcspResponder.objects.filter(active=True)
    if cert.parent_id is not None:
        qs = qs.filter(certificate__parent=cert.parent)
    responder = qs.order_by("-created_at").first()
    if responder is None:
        raise ValueError(
            "no active OCSP responder for "
            f"{cert.common_name or cert.serial}")
    return responder


def publish(broadcaster: Broadcaster, *, tables: Iterable = (),
            signing_time: Optional[_dt.datetime] = None,
            responder: Optional[OcspResponder] = None,
            refresh: _dt.timedelta = OCSP_REFRESH_DEFAULT,
            actor: str = "system") -> Tuple[PublishedSignaling, object, Optional[bytes]]:
    """Assemble and sign a customer's bundle: CDT `0x06` (+ SMT `0x07`).

    Builds the CertificationData from the ledger — the issuing/tenant CA chain,
    the CurrentCert, the distinct CDT signer, and a fresh stapled OCSP per
    certificate — and, when ``tables`` (``cdt.LlsTable`` items) is given, signs
    those LLS tables into a SignedMultiTable with the CurrentCert (A/331 6.7).
    An open rollover window adds the CertReplacement element and carries the
    NextCert (A/360 5.2.2.2).  Files are written under
    ``published/<broadcaster>/`` and a :class:`PublishedSignaling` snapshot is
    recorded.  Returns ``(snapshot, cdt_table, smt_bytes)``.
    """
    signing_time = signing_time or timezone.now()
    plan = active_rollover(broadcaster)
    # During a rollover the CurrentCert is the plan's, not merely the newest.
    current = plan.current if plan is not None else current_signer(broadcaster)
    cdt_row = cdt_signer(broadcaster)
    if current.parent is None:
        raise ValueError(
            f"signer {current.serial} has no issuing CA (parent)")
    chain_cert = current.parent

    next_row = plan.next if plan is not None else None

    ocsp_rows = [chain_cert, current, cdt_row]
    if next_row is not None:
        ocsp_rows.append(next_row)
    ocsps = []
    for row in ocsp_rows:
        # Each certificate's OCSP is signed by a responder its issuer authorized
        # (root -> tenant CA, tenant CA -> leaves); A/360 5.2.2.6 item 5.
        row_responder = (responder if responder is not None
                         else responder_for(row))
        resp = generate_ocsp(row, responder=row_responder,
                             this_update=signing_time, refresh=refresh)
        ocsps.append(Path(resp.der_file).read_bytes())

    table = pki_cdt.build(
        chain=[_load_any(chain_cert).der()],
        signing_cert=_load_any(current),
        cdt_signer_cert=_load_any(cdt_row),
        cdt_signer_key=_cert_signer(cdt_row),
        ocsp_responses=ocsps,
        ocsp_refresh=refresh,
        next_cert=_load_any(next_row) if next_row is not None else None,
        next_cert_from=plan.next_from if plan is not None else None,
        current_cert_until=plan.current_until if plan is not None else None,
        signing_time=signing_time)

    tables = tuple(tables)
    smt_bytes = None
    if tables:
        smt_bytes = pki_cdt.signed_multitable(
            list(tables), _load_any(current), _cert_signer(current),
            signing_time=signing_time)

    out_dir = base_dir() / "published" / _safe(broadcaster.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    cdt_path = out_dir / "cdt.xml.gz"
    _write_bytes(cdt_path, table.gzip_bytes())
    smt_path = ""
    if smt_bytes is not None:
        smt_path = str(out_dir / "signing.smt")
        _write_bytes(Path(smt_path), smt_bytes)
    with transaction.atomic():
        published = PublishedSignaling.objects.create(
            broadcaster=broadcaster, signing_time=signing_time, signer=current,
            cdt_file=str(cdt_path), smt_file=smt_path,
            note=f"cdt + {'smt' if smt_bytes is not None else 'no smt'}")
        audit("publish", actor=actor, target=published,
              detail={"name": broadcaster.name, "smt": smt_bytes is not None,
                      "tenant": broadcaster.tenant.slug
                      if broadcaster.tenant_id else None})
    return published, table, smt_bytes


# --- rollover (A/360 5.2.2.2 CertReplacement) ------------------------------

def start_rollover(current: Certificate, next_: Certificate,
                   next_from: _dt.datetime, current_until: _dt.datetime, *,
                   actor: str = "system") -> RolloverPlan:
    """Open a key-replacement window from ``current`` to ``next_``."""
    plan = RolloverPlan(
        broadcaster=current.broadcaster, current=current, next=next_,
        next_from=next_from, current_until=current_until,
        state=RolloverState.ACTIVE)
    plan.full_clean()
    with transaction.atomic():
        plan.save()
        audit("start-rollover", actor=actor, target=plan,
              detail={"current": current.serial, "next": next_.serial,
                      "next_from": next_from.isoformat(),
                      "current_until": current_until.isoformat()})
    return plan


def complete_rollover(plan: RolloverPlan, *, actor: str = "system") -> RolloverPlan:
    """Close the window: supersede the current cert and complete the plan."""
    with transaction.atomic():
        current = plan.current
        current.status = CertStatus.SUPERSEDED
        current.save(update_fields=["status", "updated_at"])
        plan.state = RolloverState.COMPLETED
        plan.save(update_fields=["state", "updated_at"])
        audit("complete-rollover", actor=actor, target=plan,
              detail={"current": current.serial, "next": plan.next.serial})
    return plan


# --- sales + delivery (Phase 2) --------------------------------------------

def create_checkout(order: Order, *, gateway=None, actor: str = "system") -> Order:
    """Create a Stripe Checkout Session for ``order`` and record its URL."""
    gateway = gateway or default_gateway()
    base = getattr(settings, "OPENATSC3_CA_PUBLIC_BASE_URL", "").rstrip("/")
    who = order.organization or order.email or f"order {order.pk}"
    session = gateway.create_checkout(
        amount_cents=order.amount_cents, currency=order.currency,
        description=f"ATSC 3.0 certificate bundle ({who})",
        metadata={"order": str(order.pk)},
        success_url=f"{base}/checkout/success?order={order.pk}",
        cancel_url=f"{base}/checkout/cancel?order={order.pk}")
    order.stripe_session_id = session["id"]
    order.checkout_url = session["url"]
    order.save(update_fields=["stripe_session_id", "checkout_url", "updated_at"])
    audit("create-checkout", actor=actor, target=order,
          detail={"session": session["id"]})
    return order


def mark_order_paid(order: Order, *, session_id: str = "",
                    payment_intent: str = "", actor: str = "stripe") -> Order:
    """Move a pending order to ``paid`` (the Stripe webhook transition)."""
    if order.status not in (OrderStatus.PENDING, OrderStatus.PAID):
        return order
    order.status = OrderStatus.PAID
    order.paid_at = order.paid_at or timezone.now()
    if session_id:
        order.stripe_session_id = session_id
    if payment_intent:
        order.stripe_payment_intent = payment_intent
    order.save(update_fields=["status", "paid_at", "stripe_session_id",
                              "stripe_payment_intent", "updated_at"])
    audit("order-paid", actor=actor, target=order,
          detail={"session": order.stripe_session_id,
                  "payment_intent": order.stripe_payment_intent})
    return order


def _locate_order(obj: dict) -> Optional[Order]:
    """Find the order a Stripe event object refers to (metadata, then intent)."""
    order_pk = (obj.get("metadata") or {}).get("order")
    if order_pk:
        order = Order.objects.filter(pk=order_pk).first()
        if order is not None:
            return order
    intent = obj.get("payment_intent")
    if not intent and obj.get("object") == "payment_intent":
        intent = obj.get("id")
    if intent:
        return Order.objects.filter(stripe_payment_intent=intent).first()
    session_id = obj.get("id")
    if session_id and obj.get("object") == "checkout.session":
        return Order.objects.filter(stripe_session_id=session_id).first()
    return None


def handle_webhook(payload: bytes, signature: str, *, gateway=None,
                   actor: str = "stripe") -> Optional[Order]:
    """Apply a Stripe webhook event; returns the affected order, if any.

    Acted on: ``checkout.session.completed`` (-> paid), ``charge.refunded`` /
    ``charge.dispute.created`` (-> refund, revoking the tenant by policy), and
    ``payment_intent.payment_failed`` (-> cancel an unfulfilled order).  The
    order is located by the ``order`` metadata set at checkout, falling back to
    the payment intent / session id.
    """
    gateway = gateway or default_gateway()
    event = gateway.parse_webhook(payload, signature)
    event_type = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}
    if event_type == "checkout.session.completed":
        order = _locate_order(obj)
        if order is None:
            return None
        return mark_order_paid(order, session_id=obj.get("id") or "",
                               payment_intent=obj.get("payment_intent") or "",
                               actor=actor)
    if event_type in ("charge.refunded", "charge.dispute.created"):
        order = _locate_order(obj)
        return refund_order(order, actor=actor) if order is not None else None
    if event_type == "payment_intent.payment_failed":
        order = _locate_order(obj)
        return mark_order_failed(order, actor=actor) if order is not None else None
    return None


def refund_order(order: Order, *, revoke_tenant: bool = True, reason: str = "refund",
                 actor: str = "system") -> Order:
    """Mark an order refunded and, by policy, revoke the tenant CA.

    Disabling a customer is one contained operation: revoke the tenant
    intermediate, which fails the receiver's per-cert OCSP loop for that tenant
    only ([[tenant-ca-model]]).  ``revoke_tenant=False`` records the refund
    without disabling the customer.
    """
    if order.status == OrderStatus.REFUNDED:
        return order
    order.status = OrderStatus.REFUNDED
    order.refunded_at = order.refunded_at or timezone.now()
    order.save(update_fields=["status", "refunded_at", "updated_at"])
    revoked = False
    if revoke_tenant and order.tenant_id:
        ca = _tenant_ca_cert(order.tenant)
        if ca is not None and ca.status != CertStatus.REVOKED:
            revoke(ca, reason=reason, actor=actor)
            revoked = True
    audit("refund-order", actor=actor, target=order,
          detail={"tenant": order.tenant.slug if order.tenant_id else None,
                  "revoked": revoked})
    return order


def mark_order_failed(order: Order, *, actor: str = "stripe") -> Order:
    """A failed payment on an unfulfilled order: cancel it (nothing to revoke)."""
    if order.status in (OrderStatus.PENDING, OrderStatus.PAID):
        order.status = OrderStatus.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        audit("order-failed", actor=actor, target=order)
    return order


def bundle_files(table, smt_bytes: Optional[bytes] = None) -> dict:
    """Collect a published bundle's artifacts as ``{filename: bytes}``.

    ``certs.pem`` is the full certificate set carried by the CDT (CA chain +
    CurrentCert + CDT signer [+ NextCert]), each OCSP response is written as
    ``ocsp-NN.der``, and the gzipped CDT plus the signed SMT (when present) are
    included.  A CRL (``crl.pem``) is added by the caller when a CA is known; the
    stapled OCSP remains the A/360 revocation mechanism (A/360 5.2.2.2).
    """
    files = {
        "certs.pem": b"".join(pki_x509.load_der(c).pem()
                              for c in table.certificates),
        "cdt.xml.gz": table.gzip_bytes(),
    }
    for index, der in enumerate(table.ocsp_responses):
        files[f"ocsp-{index:02d}.der"] = der
    if smt_bytes is not None:
        files["signing.smt"] = smt_bytes
    return files


def _order_slug(order: Order) -> str:
    from django.utils.text import slugify

    base = slugify(order.organization
                   or (order.email.split("@")[0] if order.email else "")) or "tenant"
    return f"{base}-{order.pk}"


def _ensure_order_tenant(order: Order) -> Tenant:
    if order.tenant_id:
        return order.tenant
    name = order.organization or order.email or _order_slug(order)
    tenant, _ = Tenant.objects.get_or_create(
        slug=_order_slug(order),
        defaults={"name": name[:128], "organization": order.organization})
    return tenant


def fulfill_order(order: Order, *, deliverer=None, actor: str = "system",
                  expires_in: Optional[int] = None) -> Order:
    """Issue the tenant CA + leaves, publish, and deliver (manual approval).

    Idempotent per tenant/leaf: existing good tenant CA, signers and responder
    are reused, so re-running after a partial failure does not duplicate them.
    """
    if order.status != OrderStatus.PAID:
        raise ValueError(f"order {order.pk} is {order.status!r}, not paid")
    if not order.bsids:
        raise ValueError(f"order {order.pk} has no bsids")
    if expires_in is None:
        expires_in = getattr(settings, "OPENATSC3_CA_DELIVERY_TTL", 3600)

    tenant = _ensure_order_tenant(order)
    if _tenant_ca_cert(tenant) is None:
        if root_is_offline():
            raise ValueError(
                f"tenant {tenant.slug!r} has no tenant CA and the root is "
                "offline; provision it by ceremony (request-tenant-ca + "
                "import-signed --profile tenant-ca) before approving this order")
        issue_tenant_ca(tenant, actor=actor)
    name = order.broadcaster_name or order.organization or tenant.slug
    bsids = tuple(int(b) for b in order.bsids)
    broadcaster, _ = Broadcaster.objects.get_or_create(
        name=name, tenant=tenant,
        defaults={"organization": order.organization or name,
                  "bsids": list(bsids)})
    if not Certificate.objects.filter(broadcaster=broadcaster,
                                      profile=Profile.SIGNALING_SIGNER,
                                      status=CertStatus.GOOD).exists():
        issue_broadcaster(name, bsids, tenant=tenant, actor=actor)
    if not Certificate.objects.filter(broadcaster=broadcaster,
                                      profile=Profile.CDT_SIGNER,
                                      status=CertStatus.GOOD).exists():
        issue_cdt_signer(name, bsids, tenant=tenant, actor=actor)
    if not OcspResponder.objects.filter(
            active=True, certificate__parent__profile=Profile.ROOT).exists():
        if root_is_offline():
            raise ValueError(
                "no root-delegated OCSP responder and the root is offline; issue "
                "one by ceremony (request-root-responder + import-signed "
                "--profile ocsp-responder --responder) before approving")
        issue_root_responder(actor=actor)
    if not OcspResponder.objects.filter(active=True,
                                        certificate__tenant=tenant).exists():
        issue_ocsp_responder(tenant=tenant, actor=actor)

    _published, table, smt = publish(broadcaster, actor=actor)
    files = bundle_files(table, smt)
    tenant_ca_row = _tenant_ca_cert(tenant)
    if tenant_ca_row is not None:
        files["crl.pem"] = pki_crl.pem(generate_crl(tenant_ca_row, actor=actor))
    url = (deliverer or default_deliverer()).deliver(
        f"order-{order.pk}", files, expires_in=expires_in)

    order.tenant = tenant
    order.broadcaster = broadcaster
    order.status = OrderStatus.FULFILLED
    order.fulfilled_at = timezone.now()
    order.delivery_url = url
    order.save(update_fields=["tenant", "broadcaster", "status", "fulfilled_at",
                              "delivery_url", "updated_at"])
    audit("fulfill-order", actor=actor, target=order,
          detail={"tenant": tenant.slug, "delivery": url})
    return order


# --- revocation ------------------------------------------------------------

def revoke(cert: Certificate, *, reason: str = "unspecified",
           when: Optional[_dt.datetime] = None, actor: str = "system") -> Certificate:
    """Mark a certificate revoked in the ledger (persistent)."""
    when = when or timezone.now()
    cert.status = CertStatus.REVOKED
    cert.revoked_at = when
    cert.revocation_reason = reason
    cert.save(update_fields=["status", "revoked_at", "revocation_reason",
                             "updated_at"])
    audit("revoke", actor=actor, target=cert,
          detail={"reason": reason, "revoked_at": when.isoformat()})
    return cert


def status_store() -> pki_ocsp.StatusStore:
    """Build an OCSP StatusStore from the persisted revocation ledger."""
    store = pki_ocsp.StatusStore()
    for cert in Certificate.objects.filter(status=CertStatus.REVOKED):
        store.mark_revoked(int(cert.serial), cert.revoked_at)
    return store


# --- OCSP generation -------------------------------------------------------

def _issuer_of(cert: Certificate) -> Certificate:
    return cert.parent or cert


def generate_ocsp(cert: Certificate, *, responder: OcspResponder,
                  this_update: Optional[_dt.datetime] = None,
                  next_update: Optional[_dt.datetime] = None,
                  refresh: _dt.timedelta = OCSP_REFRESH_DEFAULT) -> OcspResponse:
    """Issue a fresh, persisted OCSP response for one certificate."""
    this_update = this_update or timezone.now()
    next_update = next_update or (this_update + refresh)
    issuer = _issuer_of(cert)
    responder_cert = _load_any(responder.certificate)
    responder_key = _responder_signer(responder)
    issuer_cert = _load_any(issuer)
    cert_cert = _load_any(cert)

    store = status_store()
    der = pki_ocsp.respond(cert_cert, issuer_cert, responder_cert, responder_key,
                           store, this_update=this_update, next_update=next_update)
    out = _ocsp_dir() / f"{cert.serial}-{int(this_update.timestamp())}.der"
    _write_bytes(out, der)
    status = CertStatus.REVOKED if store.is_revoked(int(cert.serial)) else CertStatus.GOOD
    return OcspResponse.objects.create(
        certificate=cert, issuer=issuer, status=status, produced_at=this_update,
        this_update=this_update, next_update=next_update, der_file=str(out),
        sha256=hashlib.sha256(der).hexdigest(), responder=responder)


def generate_crl(ca_cert: Certificate, *,
                 this_update: Optional[_dt.datetime] = None,
                 next_update: Optional[_dt.datetime] = None,
                 days: int = 7, actor: str = "system") -> bytes:
    """Build a CRL for ``ca_cert`` over its revoked children (DER).

    The CRL is signed by the CA's own key (KMS or local).  Files land under
    ``<base>/crl/<serial>.crl.der`` (+ ``.pem``).  A/360 revocation is carried by
    the stapled OCSP; this is the offline-distributable companion.
    """
    revoked = [
        (int(c.serial), c.revoked_at or timezone.now(),
         c.revocation_reason or "unspecified")
        for c in Certificate.objects.filter(parent=ca_cert,
                                            status=CertStatus.REVOKED)
    ]
    der = pki_crl.build(_load_any(ca_cert), _cert_signer(ca_cert), revoked,
                        this_update=this_update, next_update=next_update, days=days)
    out_dir = base_dir() / "crl"
    _write_bytes(out_dir / f"{ca_cert.serial}.crl.der", der)
    _write_bytes(out_dir / f"{ca_cert.serial}.crl.pem", pki_crl.pem(der))
    audit("generate-crl", actor=actor, target=ca_cert,
          detail={"revoked": len(revoked)})
    return der


def _load_any(cert: Certificate) -> X509Certificate:
    data = Path(cert.der_file).read_bytes()
    if data.lstrip().startswith(b"-----"):
        return pki_x509.load_pem(data)
    return pki_x509.load_der(data)


def certificate_x509(cert: Certificate) -> X509Certificate:
    """Load the ``openatsc3_pki`` certificate object for a ledger row."""
    return _load_any(cert)


# --- export (materialize the PEM tree from the ledger) ---------------------

#: Leaf profile -> on-disk file label (mirrors ``ca.issue_broadcaster(label=...)``).
_LEAF_LABELS = {
    Profile.SIGNALING_SIGNER: "signing",
    Profile.CDT_SIGNER: "cdt-signer",
}


def _leaf_cert_path(cert: Certificate) -> Path:
    """The materialized path for a signer leaf (legacy or tenant tree)."""
    leaf = f"{_LEAF_LABELS[cert.profile]}.cert.pem"
    if cert.tenant_id:
        return (base_dir() / "tenants" / cert.tenant.slug / "broadcasters"
                / cert.broadcaster.name / leaf)
    return base_dir() / "broadcasters" / cert.broadcaster.name / leaf


def _responder_cert_path(cert: Certificate) -> Path:
    """The materialized path for an OCSP responder cert, by its authorizing tier."""
    if cert.parent_id and cert.parent.profile == Profile.ROOT:
        return base_dir() / "root" / "ocsp-responder.cert.pem"
    if cert.tenant_id:
        return base_dir() / "tenants" / cert.tenant.slug / "ocsp-responder.cert.pem"
    return base_dir() / "ocsp-responder.cert.pem"


def export_tree(*, actor: str = "system") -> List[str]:
    """Write the full PEM tree from the database (DB is authoritative).

    Materializes the classic tree (``root/root.cert.pem``,
    ``issuing/issuing.cert.pem``, legacy ``broadcasters/<name>/<label>.cert.pem``),
    the per-tenant intermediate CA tree (``tenants/<slug>/issuing.cert.pem`` and
    ``tenants/<slug>/broadcasters/<name>/<label>.cert.pem``, for both the
    signaling signer and the CDT signer), and every active OCSP responder
    certificate (root-delegated, per-tenant, and legacy), so the on-disk view
    matches the ledger.  Private keys are never rewritten — they stay where the
    issuer put them.
    """
    written: List[str] = []
    root = Certificate.objects.filter(profile=Profile.ROOT).order_by("-created_at").first()
    issuing = Certificate.objects.filter(
        profile=Profile.ISSUING_CA, status=CertStatus.GOOD).order_by("-created_at").first()
    if root is not None:
        written.append(str(_write_cert_pem(root, base_dir() / "root" / "root.cert.pem")))
    if issuing is not None:
        written.append(str(_write_cert_pem(issuing, base_dir() / "issuing" / "issuing.cert.pem")))

    for signer in Certificate.objects.filter(
            profile__in=list(_LEAF_LABELS), status=CertStatus.GOOD):
        if not signer.broadcaster_id:
            continue
        written.append(str(_write_cert_pem(signer, _leaf_cert_path(signer))))

    for tenant_ca in Certificate.objects.filter(
            profile=Profile.TENANT_CA, status=CertStatus.GOOD):
        if not tenant_ca.tenant_id:
            continue
        out = base_dir() / "tenants" / tenant_ca.tenant.slug / "issuing.cert.pem"
        written.append(str(_write_cert_pem(tenant_ca, out)))

    for responder in OcspResponder.objects.filter(active=True):
        written.append(str(_write_cert_pem(responder.certificate,
                                           _responder_cert_path(responder.certificate))))

    audit("export-tree", actor=actor, detail={"files": written})
    return written


def _write_cert_pem(cert: Certificate, path: Path) -> Path:
    data = Path(cert.der_file).read_bytes()
    pem = pki_x509.load_der(data).pem() if not data.lstrip().startswith(b"-----") else data
    _write_bytes(path, pem)
    return path


# --- import (an on-disk tree -> ledger) -------------------------------------

def _import_leaf_dir(bdir: Path, tenant: Optional[Tenant],
                     imported: List[Certificate]) -> None:
    """Import the signer leaves in a ``broadcasters/`` directory (idempotent)."""
    if not bdir.is_dir():
        return
    for child in sorted(bdir.iterdir()):
        for profile, label in _LEAF_LABELS.items():
            cert_path = child / f"{label}.cert.pem"
            if not cert_path.exists():
                continue
            cert = pki_x509.load_pem(cert_path.read_bytes())
            bsid = list(cert.bsids())
            broadcaster, _ = Broadcaster.objects.get_or_create(
                name=child.name, tenant=tenant,
                defaults={"organization": child.name, "bsids": bsid})
            if bsid and list(broadcaster.bsids) != bsid:
                broadcaster.bsids = bsid
                broadcaster.save(update_fields=["bsids"])
            key_path = child / f"{label}.key.pem"
            result = record_certificate(
                cert, profile, broadcaster=broadcaster, tenant=tenant,
                key_path=str(key_path) if key_path.exists() else "")
            imported.append(result.certificate)


def _import_responder(cert_path: Path, key_path: Path,
                      tenant: Optional[Tenant]) -> Optional[Certificate]:
    """Import one OCSP responder cert + key and ensure an active responder row."""
    if not cert_path.exists():
        return None
    cert = pki_x509.load_pem(cert_path.read_bytes())
    key_path_str = str(key_path) if key_path.exists() else ""
    result = record_certificate(cert, Profile.OCSP_RESPONDER, tenant=tenant,
                                key_path=key_path_str)
    if not OcspResponder.objects.filter(certificate=result.certificate,
                                        active=True).exists():
        OcspResponder.objects.create(
            common_name=result.certificate.common_name,
            certificate=result.certificate, key_path=key_path_str,
            key_sha256=sha256_file(key_path_str) if key_path_str else "",
            active=True)
    return result.certificate


def import_tree(path: Optional[Path] = None, *, actor: str = "migration") -> List[Certificate]:
    """Import an on-disk CA tree into the ledger (idempotent, reversible).

    Recognizes the ``CertificateAuthority`` layout: ``root/root.cert.pem``,
    ``issuing/issuing.cert.pem``, the legacy ``broadcasters/<name>/<label>.cert.pem``
    leaves, the per-tenant tree (``tenants/<slug>/issuing.cert.pem`` and
    ``tenants/<slug>/broadcasters/<name>/<label>.cert.pem``), and the OCSP
    responder certs (root-delegated, legacy, and per-tenant).
    """
    base = Path(path) if path is not None else base_dir()
    imported: List[Certificate] = []
    mapping = [
        (base / "root" / "root.cert.pem", Profile.ROOT, base / "root" / "root.key.pem"),
        (base / "issuing" / "issuing.cert.pem", Profile.ISSUING_CA,
         base / "issuing" / "issuing.key.pem"),
    ]
    for cert_path, profile, key_path in mapping:
        if cert_path.exists():
            cert = pki_x509.load_pem(cert_path.read_bytes())
            result = record_certificate(
                cert, profile, key_path=str(key_path) if key_path.exists() else "")
            imported.append(result.certificate)

    _import_leaf_dir(base / "broadcasters", None, imported)

    tenants_dir = base / "tenants"
    tenant_dirs = (sorted(d for d in tenants_dir.iterdir() if d.is_dir())
                   if tenants_dir.is_dir() else [])
    for tchild in tenant_dirs:
        tenant, _ = Tenant.objects.get_or_create(
            slug=tchild.name, defaults={"name": tchild.name})
        ca_path = tchild / "issuing.cert.pem"
        if ca_path.exists():
            ca_key = tchild / "issuing.key.pem"
            cert = pki_x509.load_pem(ca_path.read_bytes())
            result = record_certificate(
                cert, Profile.TENANT_CA, tenant=tenant,
                key_path=str(ca_key) if ca_key.exists() else "")
            imported.append(result.certificate)
        _import_leaf_dir(tchild / "broadcasters", tenant, imported)

    responder_sources = [
        (base / "root" / "ocsp-responder.cert.pem",
         base / "root" / "ocsp-responder.key.pem", None),
        (base / "ocsp-responder.cert.pem",
         base / "ocsp-responder.key.pem", None),
        (base / "issuing" / "ocsp-responder.cert.pem",
         base / "issuing" / "ocsp-responder.key.pem", None),
    ]
    for tchild in tenant_dirs:
        tenant = Tenant.objects.filter(slug=tchild.name).first()
        responder_sources.append(
            (tchild / "ocsp-responder.cert.pem",
             tchild / "ocsp-responder.key.pem", tenant))
    for cert_path, key_path, tenant in responder_sources:
        cert = _import_responder(cert_path, key_path, tenant)
        if cert is not None:
            imported.append(cert)

    audit("import-tree", actor=actor, detail={"count": len(imported),
                                              "path": str(base)})
    return imported
