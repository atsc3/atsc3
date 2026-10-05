"""Own content-protection scheme: MPEG Common Encryption (ISO/IEC 23001-7) +
our DRM system, and the A/331 security_properties_descriptor.

A/360 5.7.1 requires DRM-encrypted media to use CENC; 5.7.2 allows AES-128 in
the CTR (``cenc``) mode.  We use the standard CENC **format** but our **own DRM
system UUID** and our own key store — not Widevine, not A3SA.  The receiver
decrypts with the key it holds for the KID, so this is a self-consistent scheme
that never claims third-party interop.

This module provides:

* AES-128 CTR sample encryption/decryption (the gate is a byte-exact round
  trip);
* the ISO-BMFF boxes a player/decryptor needs — ``pssh`` (system id + KID),
  ``tenc`` (default KID + scheme), ``senc`` (per-sample IVs / KIDs);
* the DASH ``ContentProtection`` element (the ``schemeIdUri`` is our UUID);
* the A/331 Table 7.32 ``security_properties_descriptor`` (MMTP SLS) carrying
  per-asset ``scheme_code`` / ``default_KID``.

Reference: ISO/IEC 23001-7 (CENC), W3C Common PSSH box, ATSC A/331 7.2.4.1
Table 7.32, ATSC A/360 5.7.
"""

from __future__ import annotations

import struct
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

#: Our DRM system UUID (a fresh greenfield identifier, not Widevine/A3SA).
OPENATSC3_DRM_UUID = _uuid.UUID("6f70656e-6174-7363-3300-000000000001")

#: A/331 Table 7.32 descriptor_tag for security_properties_descriptor().
SECURITY_PROPERTIES_DESCRIPTOR_TAG = 0x000C

#: ISO/IEC 23001-7 protection scheme four-character codes.
SCHEME_CENC = b"cenc"
SCHEME_CBC1 = b"cbc1"
SCHEME_CENS = b"cens"
SCHEME_CBCS = b"cbcs"

#: AES block size, bytes (ISO/IEC 23001-7 sample encryption unit).
CENC_BLOCK = 16

#: A KID / IV are fixed 16 bytes in this scheme.
KID_BYTES = 16


def generate_key() -> bytes:
    """A fresh 128-bit content key (AES-128, A/360 5.7.2)."""
    import os
    return os.urandom(16)


def generate_kid() -> bytes:
    """A fresh 16-byte Key Identifier (ISO/IEC 23001-7)."""
    return _uuid.uuid4().bytes


@dataclass
class KeyStore:
    """A local KID -> content-key map (the receiver's own DRM keys)."""
    keys: Dict[bytes, bytes] = field(default_factory=dict)

    def add(self, kid: bytes, key: bytes) -> None:
        if len(kid) != KID_BYTES:
            raise ValueError("KID must be 16 bytes")
        if len(key) != 16:
            raise ValueError("CENC key must be 128 bits")
        self.keys[kid] = key

    def key_for(self, kid: bytes) -> Optional[bytes]:
        return self.keys.get(kid)

    def require(self, kid: bytes) -> bytes:
        key = self.keys.get(kid)
        if key is None:
            raise KeyError(f"no key for KID {kid.hex()}")
        return key


def _counter_iv(iv: bytes) -> bytes:
    """The 16-byte AES-CTR initial counter block for a CENC IV.

    ISO/IEC 23001-7: an 8-byte IV occupies the high 8 bytes and the block
    counter starts at 0 in the low 8 bytes; a 16-byte IV is used verbatim.
    """
    if len(iv) == 8:
        return iv + b"\x00" * 8
    if len(iv) == 16:
        return iv
    raise ValueError("CENC IV must be 8 or 16 bytes")


def encrypt_sample(sample: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128 CTR encrypt one media sample (``cenc`` mode)."""
    cipher = Cipher(algorithms.AES(key), modes.CTR(_counter_iv(iv)))
    enc = cipher.encryptor()
    return enc.update(sample) + enc.finalize()


def decrypt_sample(sample: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128 CTR decrypt one media sample (CTR is its own inverse)."""
    return encrypt_sample(sample, key, iv)


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


def _full_box(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
    header = bytes([version]) + flags.to_bytes(3, "big")
    return _box(box_type, header + payload)


def build_pssh(kid: bytes, system_id: Optional[_uuid.UUID] = None,
               data: bytes = b"") -> bytes:
    """A ``pssh`` box (ISO/IEC 23001-7 8.1): system id + one KID.

    Version 0 carries only the KID list; version 1 also carries the DRM
    system's own payload.  ``data`` selects version 1.
    """
    if len(kid) != KID_BYTES:
        raise ValueError("KID must be 16 bytes")
    sid = (system_id or OPENATSC3_DRM_UUID).bytes
    version = 1 if data else 0
    body = sid + struct.pack(">I", 1) + kid
    if version == 1:
        body += struct.pack(">I", len(data)) + data
    return _full_box(b"pssh", version, 0, body)


def parse_pssh(box: bytes) -> Tuple[bytes, List[bytes], bytes]:
    """Parse a ``pssh`` box -> ``(system_id, kids, data)``."""
    if box[4:8] != b"pssh":
        raise ValueError("not a pssh box")
    version = box[8]
    pos = 12
    system_id = box[pos:pos + 16]
    pos += 16
    count = struct.unpack(">I", box[pos:pos + 4])[0]
    pos += 4
    kids = []
    for _ in range(count):
        kids.append(box[pos:pos + 16])
        pos += 16
    data = b""
    if version > 0:
        data_len = struct.unpack(">I", box[pos:pos + 4])[0]
        pos += 4
        data = box[pos:pos + data_len]
    return system_id, kids, data


def build_schm(scheme: bytes = SCHEME_CENC, version: int = 0x00010000) -> bytes:
    """A ``schm`` SchemeTypeBox (ISO/IEC 23001-7 8.2) naming the CENC scheme."""
    if len(scheme) != 4:
        raise ValueError("scheme must be a 4-character code")
    return _full_box(b"schm", 0, 0, scheme + struct.pack(">I", version))


def parse_schm(box: bytes) -> Tuple[bytes, int]:
    """Parse a ``schm`` box -> ``(scheme, version)``."""
    if box[4:8] != b"schm":
        raise ValueError("not a schm box")
    scheme = bytes(box[12:16])
    version = struct.unpack(">I", box[16:20])[0]
    return scheme, version


def build_tenc(default_kid: bytes, iv_size: int = 8, is_protected: int = 1,
               crypt_byte_block: int = 0, skip_byte_block: int = 0,
               version: int = 1) -> bytes:
    """A ``tenc`` TrackEncryptionBox (ISO/IEC 23001-7 8.2).

    Layout: reserved, then (version > 0) crypt/skip byte blocks, then
    ``default_isProtected``, ``default_Per_Sample_IV_Size``, and the 16-byte
    ``default_KID``.  For the ``cenc`` scheme this is full-sample CTR
    (``is_protected`` 1, no pattern blocks, 8-byte IV).
    """
    if len(default_kid) != KID_BYTES:
        raise ValueError("default KID must be 16 bytes")
    body = bytes([0])  # reserved
    if version > 0:
        body += bytes([((crypt_byte_block & 0xF) << 4) | (skip_byte_block & 0xF)])
    else:
        body += bytes([0])
    body += bytes([is_protected, iv_size])
    body += default_kid
    return _full_box(b"tenc", version, 0, body)


def parse_tenc(box: bytes) -> Tuple[bytes, int, int, int]:
    """Parse a ``tenc`` box -> ``(default_kid, iv_size, is_protected, version)``."""
    if box[4:8] != b"tenc":
        raise ValueError("not a tenc box")
    version = box[8]
    pos = 12
    pos += 1  # reserved
    pos += 1  # crypt/skip (or reserved at version 0)
    is_protected = box[pos]
    pos += 1
    iv_size = box[pos]
    pos += 1
    default_kid = box[pos:pos + 16]
    return default_kid, iv_size, is_protected, version


def build_senc(ivs: Sequence[bytes], kids: Optional[Sequence[bytes]] = None,
               flags: int = 0) -> bytes:
    """A ``senc`` SampleEncryptionBox (ISO/IEC 23001-7 7.2).

    ``flags`` bit 0x2 records a per-sample KID; otherwise all samples use the
    ``tenc`` default KID.  Only whole-sample encryption (no subsamples) is
    emitted here.
    """
    body = struct.pack(">I", len(ivs))
    for i, iv in enumerate(ivs):
        body += bytes([len(iv)]) + iv
        if flags & 0x2:
            if kids is None:
                raise ValueError("per-sample KID flag set but no kids given")
            body += kids[i]
    return _full_box(b"senc", 0, flags, body)


def parse_senc(box: bytes) -> List[Tuple[bytes, Optional[bytes]]]:
    """Parse a ``senc`` box -> list of ``(iv, kid_or_None)`` per sample."""
    if box[4:8] != b"senc":
        raise ValueError("not a senc box")
    flags = int.from_bytes(box[9:12], "big")
    count = struct.unpack(">I", box[12:16])[0]
    pos = 16
    out = []
    for _ in range(count):
        iv_len = box[pos]
        pos += 1
        iv = box[pos:pos + iv_len]
        pos += iv_len
        kid = None
        if flags & 0x2:
            kid = box[pos:pos + 16]
            pos += 16
        out.append((iv, kid))
    return out


def content_protection_mpd(kid: bytes,
                           system_id: Optional[_uuid.UUID] = None) -> str:
    """A DASH ``ContentProtection`` element naming our DRM system.

    A/331 6.3 / 7.5: the ``schemeIdUri`` is the DRM system id as a UUID URN,
    matching SLT ``@drmSystemID``; the KID is carried in ``cenc:default_KID``.
    """
    sid = system_id or OPENATSC3_DRM_UUID
    return (f'<ContentProtection schemeIdUri="urn:uuid:{sid}" '
            f'value="cenc" '
            f'cenc:default_KID="{_uuid.UUID(bytes=kid)}" '
            f'xmlns:cenc="urn:mpeg:cenc:2013"/>')


@dataclass(frozen=True)
class ProtectedAsset:
    """One DRM-protected asset row (A/331 Table 7.32)."""
    asset_id: bytes
    scheme_code: Optional[bytes] = None
    default_kid: Optional[bytes] = None


def security_properties_descriptor(assets: Sequence[ProtectedAsset]) -> bytes:
    """Build a security_properties_descriptor() (A/331 7.2.4.1 Table 7.32)."""
    if not assets:
        raise ValueError("at least one protected asset is required")
    body = struct.pack(">B", len(assets))
    for asset in assets:
        body += struct.pack(">I", len(asset.asset_id)) + asset.asset_id
        flags = 0
        if asset.scheme_code is not None:
            flags |= 0x80
        if asset.default_kid is not None:
            flags |= 0x40
        body += bytes([flags])
        if asset.scheme_code is not None:
            if len(asset.scheme_code) != 4:
                raise ValueError("scheme_code must be a 4-character code")
            body += asset.scheme_code
        if asset.default_kid is not None:
            if len(asset.default_kid) != KID_BYTES:
                raise ValueError("default_KID must be 16 bytes")
            body += bytes([len(asset.default_kid)]) + asset.default_kid
    return struct.pack(">HH", SECURITY_PROPERTIES_DESCRIPTOR_TAG,
                       len(body)) + body


def parse_security_properties_descriptor(data: bytes) -> List[ProtectedAsset]:
    """Parse a security_properties_descriptor() (A/331 Table 7.32)."""
    tag, length = struct.unpack(">HH", data[:4])
    if tag != SECURITY_PROPERTIES_DESCRIPTOR_TAG:
        raise ValueError(f"unexpected descriptor_tag 0x{tag:04x}")
    pos = 4
    end = 4 + length
    number = data[pos]
    pos += 1
    out = []
    for _ in range(number):
        asset_len = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        asset_id = data[pos:pos + asset_len]
        pos += asset_len
        flags = data[pos]
        pos += 1
        scheme = None
        kid = None
        if flags & 0x80:
            scheme = data[pos:pos + 4]
            pos += 4
        if flags & 0x40:
            kid_len = data[pos]
            pos += 1
            kid = data[pos:pos + kid_len]
            pos += kid_len
        out.append(ProtectedAsset(asset_id=asset_id, scheme_code=scheme,
                                  default_kid=kid))
    if pos != end:
        raise ValueError("security_properties_descriptor length mismatch")
    return out
