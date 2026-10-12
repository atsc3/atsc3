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
from openatsc3_pki import keys as pki_keys
from openatsc3_pki import ocsp as pki_ocsp
from openatsc3_pki import x509 as pki_x509
from openatsc3_pki.x509 import Certificate as X509Certificate
from openatsc3_pki.x509 import SubjectInfo
from cryptography import x509 as _x509
from cryptography.x509.oid import ExtensionOID, NameOID

from .constants import OCSP_REFRESH_DEFAULT, CertStatus, Profile
from .models import (
    AuditEvent,
    Broadcaster,
    Certificate,
    OcspResponder,
    OcspResponse,
    Tenant,
)


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


def _responder_signer(responder: OcspResponder):
    """The OCSP responder's signing key: a KMS backend or the on-disk key."""
    if responder.certificate.kms_key_id:
        from openatsc3_pki.signer import KmsSigner
        return KmsSigner.for_key(responder.certificate.kms_key_id)
    return pki_keys.load_private(responder.key_path)


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


def issue_broadcaster(name: str, bsids: Iterable[int], *,
                      tenant: Optional[Tenant] = None,
                      common_name: Optional[str] = None, days: int = 365,
                      organization: Optional[str] = None,
                      kms_key_id: str = "", key=None,
                      actor: str = "system") -> Certificate:
    bsids = tuple(int(b) for b in bsids)
    defaults = {"organization": organization or name, "bsids": list(bsids)}
    if tenant is not None:
        defaults["tenant"] = tenant
    broadcaster, _ = Broadcaster.objects.get_or_create(name=name, defaults=defaults)
    updates = []
    if list(bsids) and list(broadcaster.bsids) != list(bsids):
        broadcaster.bsids = list(bsids)
        updates.append("bsids")
    if tenant is not None and broadcaster.tenant_id != tenant.id:
        broadcaster.tenant = tenant
        updates.append("tenant")
    if updates:
        broadcaster.save(update_fields=updates)
    authority = _authority()
    tenant_slug = tenant.slug if tenant is not None else None
    signer = _signer_for(key, kms_key_id)
    ca_key = _tenant_ca_signer(tenant) if tenant is not None else None
    cert = authority.issue_broadcaster(name, bsids, common_name=common_name,
                                       days=days, tenant=tenant_slug,
                                       key=signer, ca_key=ca_key)
    key_path = ("" if signer is not None else
                os.path.join(authority.broadcaster_dir(name, tenant_slug),
                             "signing.key.pem"))
    with transaction.atomic():
        result = record_certificate(cert, Profile.SIGNALING_SIGNER,
                                    key_path=key_path, kms_key_id=kms_key_id,
                                    broadcaster=broadcaster, tenant=tenant)
        audit("issue-broadcaster", actor=actor, target=result.certificate,
              detail={"name": name, "bsids": list(bsids),
                      "tenant": tenant_slug})
    return result.certificate


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


def _load_any(cert: Certificate) -> X509Certificate:
    data = Path(cert.der_file).read_bytes()
    if data.lstrip().startswith(b"-----"):
        return pki_x509.load_pem(data)
    return pki_x509.load_der(data)


def certificate_x509(cert: Certificate) -> X509Certificate:
    """Load the ``openatsc3_pki`` certificate object for a ledger row."""
    return _load_any(cert)


# --- export (materialize the PEM tree from the ledger) ---------------------

def export_tree(*, actor: str = "system") -> List[str]:
    """Write the classic PEM tree from the database (DB is authoritative).

    Produces ``root/root.cert.pem``, ``issuing/issuing.cert.pem`` and
    ``broadcasters/<name>/signing.cert.pem`` for the current GOOD certificates.
    Private keys are never rewritten — they stay where the issuer put them.
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
            profile=Profile.SIGNALING_SIGNER, status=CertStatus.GOOD):
        if not signer.broadcaster_id:
            continue
        name = signer.broadcaster.name
        written.append(str(_write_cert_pem(
            signer, base_dir() / "broadcasters" / name / "signing.cert.pem")))
    audit("export-tree", actor=actor, detail={"files": written})
    return written


def _write_cert_pem(cert: Certificate, path: Path) -> Path:
    data = Path(cert.der_file).read_bytes()
    pem = pki_x509.load_der(data).pem() if not data.lstrip().startswith(b"-----") else data
    _write_bytes(path, pem)
    return path


# --- import (the legacy on-disk tree -> ledger) ----------------------------

def import_tree(path: Optional[Path] = None, *, actor: str = "migration") -> List[Certificate]:
    """Import a legacy CA tree into the ledger (idempotent, reversible).

    Recognizes the ``CertificateAuthority`` layout: ``root/root.cert.pem``,
    ``issuing/issuing.cert.pem`` and ``broadcasters/<name>/signing.cert.pem``.
    """
    base = Path(path) if path is not None else base_dir()
    imported: List[Certificate] = []
    mapping = [
        (base / "root" / "root.cert.pem", Profile.ROOT, base / "root" / "root.key.pem", None),
        (base / "issuing" / "issuing.cert.pem", Profile.ISSUING_CA,
         base / "issuing" / "issuing.key.pem", None),
    ]
    for cert_path, profile, key_path, broadcaster in mapping:
        if cert_path.exists():
            cert = pki_x509.load_pem(cert_path.read_bytes())
            result = record_certificate(
                cert, profile, key_path=str(key_path) if key_path.exists() else "",
                broadcaster=broadcaster)
            imported.append(result.certificate)

    bdir = base / "broadcasters"
    if bdir.is_dir():
        for child in sorted(bdir.iterdir()):
            cert_path = child / "signing.cert.pem"
            if not cert_path.exists():
                continue
            cert = pki_x509.load_pem(cert_path.read_bytes())
            bsid = list(cert.bsids())
            broadcaster, _ = Broadcaster.objects.get_or_create(
                name=child.name,
                defaults={"organization": child.name, "bsids": bsid})
            if bsid and list(broadcaster.bsids) != bsid:
                broadcaster.bsids = bsid
                broadcaster.save(update_fields=["bsids"])
            result = record_certificate(
                cert, Profile.SIGNALING_SIGNER,
                key_path=str(child / "signing.key.pem")
                if (child / "signing.key.pem").exists() else "",
                broadcaster=broadcaster)
            imported.append(result.certificate)

    audit("import-tree", actor=actor, detail={"count": len(imported),
                                              "path": str(base)})
    return imported
