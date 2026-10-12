"""On-disk certificate authority: root, issuing CA, broadcaster signers.

Layout created under a base directory::

    <base>/root/root.key.pem            root private key (0600)
    <base>/root/root.cert.pem          self-signed root certificate
    <base>/issuing/issuing.key.pem     legacy default issuing CA private key
    <base>/issuing/issuing.cert.pem    legacy default issuing CA certificate
    <base>/broadcasters/<name>/signing.{key,cert}.pem
    <base>/tenants/<tenant>/issuing.{key,cert}.pem          per-tenant CA
    <base>/tenants/<tenant>/broadcasters/<name>/signing.{key,cert}.pem

The single top-level ``issuing/`` tree is the legacy/default authority kept for
backward compatibility.  Each customer gets a **per-tenant intermediate CA**
under ``tenants/<tenant>/``; revoking it disables that customer only.  Root and
tenant-CA signing keys may be external (:class:`~openatsc3_pki.signer.Signer`,
e.g. AWS KMS), in which case no private key file is written.

All key material is written owner-only.  This is a greenfield, self-contained
trust anchor: the receiver is configured to trust only ``root.cert.pem``
(fail-closed).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from cryptography.x509.oid import NameOID

from . import keys, x509 as _x509
from .x509 import Certificate, SubjectInfo

_DEFAULT_ORG = "OpenATSC3"


def _safe(name: str) -> str:
    """Filesystem-safe path element for a tenant or broadcaster name."""
    return name.replace("/", "_").replace("..", "_")


@dataclass
class CertificateAuthority:
    """A file-backed root + issuing CA plus per-broadcaster signer issuance."""
    base_dir: str

    @property
    def root_dir(self) -> str:
        return os.path.join(self.base_dir, "root")

    @property
    def issuing_dir(self) -> str:
        return os.path.join(self.base_dir, "issuing")

    @property
    def tenants_dir(self) -> str:
        return os.path.join(self.base_dir, "tenants")

    @property
    def root_key_path(self) -> str:
        return os.path.join(self.root_dir, "root.key.pem")

    @property
    def root_cert_path(self) -> str:
        return os.path.join(self.root_dir, "root.cert.pem")

    @property
    def issuing_key_path(self) -> str:
        return os.path.join(self.issuing_dir, "issuing.key.pem")

    @property
    def issuing_cert_path(self) -> str:
        return os.path.join(self.issuing_dir, "issuing.cert.pem")

    def tenant_dir(self, tenant: str) -> str:
        return os.path.join(self.tenants_dir, _safe(tenant))

    def tenant_key_path(self, tenant: str) -> str:
        return os.path.join(self.tenant_dir(tenant), "issuing.key.pem")

    def tenant_cert_path(self, tenant: str) -> str:
        return os.path.join(self.tenant_dir(tenant), "issuing.cert.pem")

    def broadcaster_dir(self, name: str, tenant: Optional[str] = None) -> str:
        parent = self.tenant_dir(tenant) if tenant else self.base_dir
        return os.path.join(parent, "broadcasters", _safe(name))

    def init_root(self, common_name: str = "OpenATSC3 Root CA",
                  organization: str = _DEFAULT_ORG, days: int = 7300,
                  password: Optional[bytes] = None) -> Certificate:
        """Create the self-signed root (A/360 5.3.1.2)."""
        subject = SubjectInfo(common_name=common_name,
                               organizational_unit="ATSC Trust Authority",
                               organization=organization)
        key = keys.generate(keys.ROOT_CURVE)
        cert = _x509.issue_root(subject, key, days=days)
        keys.write_private(self.root_key_path, key, password)
        os.makedirs(self.root_dir, exist_ok=True)
        cert.to_pem_file(self.root_cert_path)
        return cert

    def load_root(self, password: Optional[bytes] = None) -> Certificate:
        from .x509 import load_pem
        with open(self.root_cert_path, "rb") as fh:
            return load_pem(fh.read())

    def load_root_key(self, password: Optional[bytes] = None) -> keys.KeyPair:
        return keys.load_private(self.root_key_path, password)

    def issue_issuing(self, common_name: str = "OpenATSC3 Issuing CA",
                      days: int = 3650,
                      password: Optional[bytes] = None,
                      key: "Optional[_x509.KeyLike]" = None) -> Certificate:
        """Create an issuing CA signed by the root (A/360 5.3.1.3)."""
        root = self.load_root()
        root_key = self.load_root_key()
        org = root.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value
        subject = SubjectInfo(common_name=common_name,
                               organizational_unit="ATSC Trust Authority",
                               organization=org)
        if key is None:
            key = keys.generate(keys.CA_CURVE)
            keys.write_private(self.issuing_key_path, key, password)
        cert = _x509.issue_ca(subject, key, root, root_key, days=days)
        os.makedirs(self.issuing_dir, exist_ok=True)
        cert.to_pem_file(self.issuing_cert_path)
        return cert

    def load_issuing(self) -> Certificate:
        from .x509 import load_pem
        with open(self.issuing_cert_path, "rb") as fh:
            return load_pem(fh.read())

    def load_issuing_key(self, password: Optional[bytes] = None) -> keys.KeyPair:
        return keys.load_private(self.issuing_key_path, password)

    def issue_tenant_ca(self, tenant: str, common_name: Optional[str] = None,
                        days: int = 3650, key: "Optional[_x509.KeyLike]" = None,
                        root: "Optional[Certificate]" = None,
                        root_key: "Optional[_x509.KeyLike]" = None,
                        password: Optional[bytes] = None) -> Certificate:
        """Create a per-tenant intermediate CA signed by the root (A/360 5.3.1.3).

        ``key`` is the tenant CA's signing key — a local
        :class:`~openatsc3_pki.keys.KeyPair` (generated and written under
        ``tenants/<tenant>/`` when omitted) or an external
        :class:`~openatsc3_pki.signer.Signer` such as a KMS key (then no private
        key file is written).  ``root``/``root_key`` default to the offline root
        on disk.  Revoking this certificate disables one customer.
        """
        if root is None:
            root = self.load_root()
        if root_key is None:
            root_key = self.load_root_key()
        org = root.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value
        subject = SubjectInfo(
            common_name=common_name or f"{tenant} Intermediate CA",
            organizational_unit="ATSC Trust Authority",
            organization=org)
        if key is None:
            key = keys.generate(keys.CA_CURVE)
            keys.write_private(self.tenant_key_path(tenant), key, password)
        cert = _x509.issue_tenant_ca(subject, key, root, root_key, days=days)
        os.makedirs(self.tenant_dir(tenant), exist_ok=True)
        cert.to_pem_file(self.tenant_cert_path(tenant))
        return cert

    def load_tenant(self, tenant: str) -> Certificate:
        from .x509 import load_pem
        with open(self.tenant_cert_path(tenant), "rb") as fh:
            return load_pem(fh.read())

    def load_tenant_key(self, tenant: str,
                        password: Optional[bytes] = None) -> keys.KeyPair:
        return keys.load_private(self.tenant_key_path(tenant), password)

    def issue_broadcaster(self, name: str, bsids: Tuple[int, ...],
                          common_name: Optional[str] = None,
                          days: int = 365,
                          password: Optional[bytes] = None, *,
                          tenant: Optional[str] = None,
                          key: "Optional[_x509.KeyLike]" = None,
                          ca_key: "Optional[_x509.KeyLike]" = None) -> Certificate:
        """Issue a signaling-signer certificate for one broadcaster.

        ``tenant`` selects the issuing CA: ``None`` uses the legacy default
        ``issuing/`` tree, otherwise the per-tenant intermediate under
        ``tenants/<tenant>/``.  ``key`` is the signer's own signing key and
        ``ca_key`` the issuing CA's signer — both may be external
        :class:`~openatsc3_pki.signer.Signer` backends (e.g. KMS), in which case
        no private key file is written for that key.
        """
        if tenant is None:
            issuing = self.load_issuing()
            issuing_key = ca_key if ca_key is not None else self.load_issuing_key()
        else:
            issuing = self.load_tenant(tenant)
            issuing_key = ca_key if ca_key is not None else self.load_tenant_key(tenant)
        subject = SubjectInfo(
            common_name=common_name or f"{name}-LLS-Signer",
            organizational_unit="ATSC Broadcast Signaling Signer",
            organization=name)
        if key is None:
            key = keys.generate(keys.SIGNER_CURVE)
            wrote_key = True
        else:
            wrote_key = False
        cert = _x509.issue_signaling_signer(
            subject, key, issuing, issuing_key, bsids, days=days)
        out = self.broadcaster_dir(name, tenant)
        os.makedirs(out, exist_ok=True)
        if wrote_key:
            keys.write_private(os.path.join(out, "signing.key.pem"), key, password)
        cert.to_pem_file(os.path.join(out, "signing.cert.pem"))
        return cert

    def load_broadcaster(self, name: str,
                         tenant: Optional[str] = None) -> Certificate:
        from .x509 import load_pem
        with open(os.path.join(self.broadcaster_dir(name, tenant),
                               "signing.cert.pem"), "rb") as fh:
            return load_pem(fh.read())

    def load_broadcaster_key(self, name: str,
                             password: Optional[bytes] = None,
                             tenant: Optional[str] = None) -> keys.KeyPair:
        return keys.load_private(
            os.path.join(self.broadcaster_dir(name, tenant), "signing.key.pem"),
            password)
