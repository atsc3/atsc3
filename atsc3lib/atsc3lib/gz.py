"""Bounded gzip decompression (RFC 1952).

LLS/SLS payloads are gzip-compressed (A/331 6.3, A.3.3.4, 7.2.3.1) and arrive
from the air, so decompression must be bounded: a malformed or hostile stream
must not inflate without limit.  ``decompress_bounded`` reads at most
``max_bytes`` of output and raises :class:`BoundedDecompressionError` when the
stream would exceed it, rather than buffering the whole result.
"""

import zlib
from typing import Optional

#: gzip magic (RFC 1952).
GZIP_MAGIC = b"\x1f\x8b"

#: Reasonable ceiling for a signaling fragment (SLT/USBD/HELD are a few kB);
#: callers may pass a tighter spec-derived bound.
DEFAULT_MAX_BYTES = 8 << 20


class BoundedDecompressionError(ValueError):
    """A compressed stream would inflate past the allowed bound."""


def decompress_bounded(data: bytes,
                       max_bytes: int = DEFAULT_MAX_BYTES,
                       is_gzip: Optional[bool] = None) -> bytes:
    """Inflate ``data`` (gzip when it has the magic) capped at ``max_bytes``.

    When ``is_gzip`` is None the format is chosen by the RFC 1952 magic, so an
    uncompressed payload is returned unchanged.
    """
    if is_gzip is None:
        is_gzip = data[:2] == GZIP_MAGIC
    if not is_gzip:
        if len(data) > max_bytes:
            raise BoundedDecompressionError(
                f"uncompressed payload {len(data)} exceeds bound {max_bytes}")
        return data

    wbits = 16 + zlib.MAX_WBITS
    obj = zlib.decompressobj(wbits)
    out = obj.decompress(data, max_bytes)
    if obj.unconsumed_tail:
        raise BoundedDecompressionError(
            f"gzip stream exceeds bound {max_bytes} bytes")
    out += obj.flush()
    if len(out) > max_bytes:
        raise BoundedDecompressionError(
            f"gzip stream exceeds bound {max_bytes} bytes")
    return out
