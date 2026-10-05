"""ATSC A/360 Subject Directory Attribute (id-atsc-sdattr-bsid) encoding.

A/360 5.3.1.6 requires a broadcast signaling signer certificate to carry a
Subject Directory Attributes extension whose attribute of type
``id-atsc-sdattr-bsid`` (1.3.6.1.4.1.51552.9.1) has values that are a
**SET OF INTEGER**, one integer per Broadcast Stream Identifier (bsid).

``cryptography`` parses SubjectDirectoryAttributes as an opaque
``UnrecognizedExtension``, so this module encodes/decodes the DER directly.
Gated by the published ATSC CertificationData example (single bsid 33) and by
round-tripping the multi-bsid set.
"""

from __future__ import annotations

from typing import Iterable, Tuple

from asn1crypto.core import (
    Any,
    Integer,
    ObjectIdentifier,
    Sequence,
    SequenceOf,
    SetOf,
)

from .oids import ID_ATSC_SDATTR_BSID


class _SetOfAny(SetOf):
    _child_spec = Any


class _Attribute(Sequence):
    _fields = [
        ("type", ObjectIdentifier),
        ("values", _SetOfAny),
    ]


class SubjectDirectoryAttributes(SequenceOf):
    _child_spec = _Attribute


def encode_bsid_sda(bsids: Iterable[int]) -> bytes:
    """DER of a SubjectDirectoryAttributes extension for ``bsids``.

    The returned bytes are the value of the X.509 SubjectDirectoryAttributes
    extension (OID 2.5.29.9).  Raises ``ValueError`` for an empty set or a
    value outside 0..65535 (a bsid is a 16-bit identifier).
    """
    values = [int(b) for b in bsids]
    if not values:
        raise ValueError("at least one bsid is required")
    for b in values:
        if not 0 <= b <= 0xFFFF:
            raise ValueError(f"bsid {b} is not a 16-bit value")
    attr = _Attribute(
        {"type": ID_ATSC_SDATTR_BSID, "values": _SetOfAny([Integer(v) for v in values])}
    )
    return SubjectDirectoryAttributes([attr]).dump()


def decode_bsid_sda(der: bytes) -> Tuple[int, ...]:
    """The bsid set from a SubjectDirectoryAttributes extension value."""
    parsed = SubjectDirectoryAttributes.load(der)
    out = []
    for attr in parsed:
        if attr["type"].dotted != ID_ATSC_SDATTR_BSID:
            continue
        out.extend(int(v.native) for v in attr["values"])
    return tuple(sorted(out))
