"""OpenATSC3 greenfield PKI: ATSC A/360 certificate profiles and CMS signing.

This package is a self-contained certificate authority and content-protection
toolkit that runs *alongside* A3SA as a parallel trust anchor.  It follows the
certificate formats and validation rules defined in A/360 and A/331, but owns
its root and its content-protection scheme independently.
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

from . import ca, cdt, ceremony, cms, content, crl, keys, ocsp, oids, quorum, rotation, sda, signer, verify, x509

try:
    __version__ = _dist_version("openatsc3-pki")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["ca", "cdt", "ceremony", "cms", "content", "crl", "keys", "ocsp", "oids", "quorum", "rotation", "sda",
           "signer", "verify", "x509"]
