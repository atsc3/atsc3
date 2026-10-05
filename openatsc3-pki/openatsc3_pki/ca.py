"""On-disk certificate authority: root, issuing CA, broadcaster signers.

Layout created under a base directory::

    <base>/root/root.key.pem            root private key (0600)
    <base>/root/root.cert.pem          self-signed root certificate
    <base>/issuing/issuing.key.pem     issuing CA private key
    <base>/issuing/issuing.cert.pem    issuing CA certificate
    <base>/broadcasters/<name>/signing.key.pem
    <base>/broadcasters/<name>/signing.cert.pem

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

    def broadcaster_dir(self, name: str) -> str:
        safe = name.replace("/", "_").replace("..", "_")
        return os.path.join(self.base_dir, "broadcasters", safe)

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
                      password: Optional[bytes] = None) -> Certificate:
        """Create an issuing CA signed by the root (A/360 5.3.1.3)."""
        root = self.load_root()
        root_key = self.load_root_key()
        org = root.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value
        subject = SubjectInfo(common_name=common_name,
                               organizational_unit="ATSC Trust Authority",
                               organization=org)
        key = keys.generate(keys.CA_CURVE)
        cert = _x509.issue_ca(subject, key, root, root_key, days=days)
        keys.write_private(self.issuing_key_path, key, password)
        os.makedirs(self.issuing_dir, exist_ok=True)
        cert.to_pem_file(self.issuing_cert_path)
        return cert

    def load_issuing(self) -> Certificate:
        from .x509 import load_pem
        with open(self.issuing_cert_path, "rb") as fh:
            return load_pem(fh.read())

    def load_issuing_key(self, password: Optional[bytes] = None) -> keys.KeyPair:
        return keys.load_private(self.issuing_key_path, password)

    def issue_broadcaster(self, name: str, bsids: Tuple[int, ...],
                          common_name: Optional[str] = None,
                          days: int = 365,
                          password: Optional[bytes] = None) -> Certificate:
        """Issue a signaling-signer certificate for one broadcaster.

        Returns the new end-entity certificate; the private key is written
        under ``broadcasters/<name>/``.
        """
        issuing = self.load_issuing()
        issuing_key = self.load_issuing_key()
        subject = SubjectInfo(
            common_name=common_name or f"{name}-LLS-Signer",
            organizational_unit="ATSC Broadcast Signaling Signer",
            organization=name)
        key = keys.generate(keys.SIGNER_CURVE)
        cert = _x509.issue_signaling_signer(
            subject, key, issuing, issuing_key, bsids, days=days)
        out = self.broadcaster_dir(name)
        keys.write_private(os.path.join(out, "signing.key.pem"), key, password)
        cert.to_pem_file(os.path.join(out, "signing.cert.pem"))
        return cert

    def load_broadcaster(self, name: str) -> Certificate:
        from .x509 import load_pem
        with open(os.path.join(self.broadcaster_dir(name),
                               "signing.cert.pem"), "rb") as fh:
            return load_pem(fh.read())

    def load_broadcaster_key(self, name: str,
                             password: Optional[bytes] = None) -> keys.KeyPair:
        return keys.load_private(
            os.path.join(self.broadcaster_dir(name), "signing.key.pem"),
            password)
