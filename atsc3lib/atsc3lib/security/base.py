"""Provider seam for ATSC 3.0 signaling-security verification.

The receiver must not hard-wire one certificate authority or DRM vendor.  The
standards define the *formats and validation rules* (A/360 5.2.2, A/331 6.7)
but not the operator, so the crypto backend is a pluggable *security provider*.

A receiver-side provider answers three questions about the LLS security tables
a PLP delivers:

* :meth:`SecurityProvider.verify_certification_data` -- is the CertificationData
  table (LLS ``0x06``) authentic against my trust anchors?
* :meth:`SecurityProvider.verify_signed_table` -- is this signed table
  (LLS ``0x07`` SignedMultiTable, or an SLS fragment) authentic, signed by a key
  the certification data authenticates?
* :meth:`SecurityProvider.load_trust_anchors` -- load my configured trust store.

The cryptography lives in the provider; ``atsc3lib`` only carries the result
types.  Providers can be registered by name and selected with
``ATSC3LIB_SECURITY_PROVIDER`` or :func:`set_provider`; the built-in provider is
the greenfield ``openatsc3_pki`` (our own CA), and an A3SA/Widevine-backed one
can be dropped in without touching the receiver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence, runtime_checkable

#: LLS table id of the CertificationData table (A/360 5.2.2.2).
CDT_TABLE_ID = 0x06

#: LLS table id of the SignedMultiTable (A/331 6.7).
SIGNED_MULTITABLE_ID = 0x07

#: An LLS table id -> short name (A/331 Table 6.1).
LLS_TABLE_NAMES = {0x01: "SLT", 0x07: "SignedMultiTable", 0x06: "CertificationData"}


@dataclass(frozen=True)
class TableVerdict:
    """The verification outcome for one received table."""
    table_id: int
    name: str
    ok: bool
    reason: str = ""
    signing_time: Optional[object] = None
    signer_ski: Optional[bytes] = None


@dataclass
class SecurityReport:
    """The result of verifying every security-relevant table in a PLP.

    Attributes:
        cdt_ok: the CertificationData table verified against the trust anchors.
        verdicts: per-table verdicts for the signed tables present.
        handle: the provider's opaque verified state (e.g. a verified key set),
            needed to verify later messages, when the CDT passed.
        provider: the name of the provider that produced this report.
        reason: why the report is not ok, when it is not.
    """
    cdt_ok: bool = False
    verdicts: List[TableVerdict] = field(default_factory=list)
    handle: object = None
    provider: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        """True when a CDT verified and every signed table verified."""
        if not self.cdt_ok:
            return False
        return all(v.ok for v in self.verdicts)

    @property
    def signed_tables(self) -> List[TableVerdict]:
        return self.verdicts

    @property
    def certified(self):
        """Alias for :attr:`handle` (the verified certification state)."""
        return self.handle


@dataclass(frozen=True)
class SignableTable:
    """One table carried for security verification.

    The provider receives these generic rows rather than an ``atsc3lib``
    ``LlsTable`` so it need not depend on the receiver's I/O types.
    """
    table_id: int
    name: str
    data: bytes
    version: int = 0


@runtime_checkable
class SecurityProvider(Protocol):
    """A pluggable verification backend (own CA, A3SA, ...).

    Implementations must be fail-closed: an unverifiable table is ``ok=False``.
    """

    #: Stable registry name.
    name: str

    def load_trust_anchors(self, paths: Sequence[str]) -> list:
        """Load trust anchors from PEM files (or provider-specific config)."""
        ...

    def verify_certification_data(self, table: SignableTable, anchors: Sequence,
                                  now=None):
        """Verify the CertificationData table.

        Returns ``(Verdict, handle)`` where ``handle`` is opaque provider state
        for :meth:`verify_signed_table`, or None on failure.
        """
        ...

    def verify_signed_table(self, table: SignableTable, handle, *,
                            expected_bsids: Optional[Sequence[int]] = None,
                            now=None, previous_signing_time=None) -> TableVerdict:
        """Verify one signed table against a verified certification handle."""
        ...


_REGISTRY: Dict[str, SecurityProvider] = {}
_ACTIVE: Optional[SecurityProvider] = None


def register(provider: SecurityProvider) -> SecurityProvider:
    """Register a provider under its :attr:`SecurityProvider.name`."""
    if not getattr(provider, "name", None):
        raise ValueError("provider must define a non-empty name")
    _REGISTRY[provider.name] = provider
    return provider


def available() -> List[str]:
    """The names of the registered providers."""
    _ensure_builtin()
    return sorted(_REGISTRY)


def get_provider(name: Optional[str] = None) -> SecurityProvider:
    """Return the active provider, or the named one.

    With no name the active provider is returned; it is chosen by
    :func:`set_provider`, else the ``ATSC3LIB_SECURITY_PROVIDER`` environment
    variable, else the built-in greenfield provider.
    """
    _ensure_builtin()
    global _ACTIVE
    if name is not None:
        if name not in _REGISTRY:
            raise KeyError(f"unknown security provider {name!r}; "
                           f"registered: {sorted(_REGISTRY)}")
        return _REGISTRY[name]
    if _ACTIVE is None:
        import os
        env = os.environ.get("ATSC3LIB_SECURITY_PROVIDER")
        if env is not None:
            if env not in _REGISTRY:
                raise KeyError(f"unknown security provider {env!r}; "
                               f"registered: {sorted(_REGISTRY)}")
            _ACTIVE = _REGISTRY[env]
        elif DEFAULT_PROVIDER in _REGISTRY:
            _ACTIVE = _REGISTRY[DEFAULT_PROVIDER]
        else:
            raise RuntimeError(
                "no security provider available; install one "
                "(e.g. pip install -e .[pki]) or register a provider")
    return _ACTIVE


def set_provider(provider) -> SecurityProvider:
    """Select the active provider (by instance or by registered name)."""
    global _ACTIVE
    _ensure_builtin()
    if isinstance(provider, str):
        if provider not in _REGISTRY:
            raise KeyError(f"unknown security provider {provider!r}; "
                           f"registered: {sorted(_REGISTRY)}")
        _ACTIVE = _REGISTRY[provider]
    else:
        register(provider)
        _ACTIVE = provider
    return _ACTIVE


#: The provider used when nothing else is configured.  Imported lazily so the
#: base module never depends on a particular crypto package.
DEFAULT_PROVIDER = "openatsc3"

_BUILTIN_LOADED = False


def _ensure_builtin() -> None:
    global _BUILTIN_LOADED
    if _BUILTIN_LOADED:
        return
    _BUILTIN_LOADED = True
    try:
        from . import openatsc3  # noqa: F401  (registers itself)
    except ImportError:
        # Without a provider package the receiver still runs; verification
        # simply reports "no provider available".
        pass
