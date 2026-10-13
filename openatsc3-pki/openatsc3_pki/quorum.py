"""Shamir secret sharing over GF(256): the air-gapped root's quorum custody.

The root key is never usable by a single custodian.  At generation it is split
into ``n`` shares with threshold ``k``; reconstructing the key requires any ``k``
of them, and ``k-1`` reveal nothing.  This gives the root a **3-of-5 quorum**:
three custodians (or three storage media) must convene to sign, which is the
control that makes a compromised root key require more than one failure.

This is Shamir secret sharing (each byte is the constant term of an independent
degree-``k-1`` polynomial over GF(256)); it is *not* threshold signing -- ``k``
shares are combined in memory at ceremony time on an air-gapped host.  For a rare
root signing ceremony that is the standard software-root custody model.

Each share is ``b"\\x00"*0 + bytes([x]) + y`` where ``x`` is a distinct non-zero
field element (1..255) and ``y`` is the shared value; the leading ``x`` lets
:func:`combine` recover which evaluation point each share is.
"""

from __future__ import annotations

import secrets
from typing import Callable, List, Sequence

#: GF(256) reduction (low byte of x^8 + x^4 + x^3 + x + 1).
_POLY = 0x1B

#: The largest quorum supported (non-zero GF(256) evaluation points).
MAX_SHARES = 255


def _mul(a: int, b: int) -> int:
    """Carry-less multiply in GF(256), reduced by the AES polynomial."""
    product = 0
    for _ in range(8):
        if b & 1:
            product ^= a
        high = a & 0x80
        a = (a << 1) & 0xFF
        if high:
            a ^= _POLY
        b >>= 1
    return product


def _pow(a: int, exponent: int) -> int:
    result = 1
    while exponent:
        if exponent & 1:
            result = _mul(result, a)
        a = _mul(a, a)
        exponent >>= 1
    return result


def _inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("no inverse of zero in GF(256)")
    return _pow(a, 254)  # a^255 == 1 for a != 0


def _div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("division by zero in GF(256)")
    return 0 if a == 0 else _mul(a, _inv(b))


def split(secret: bytes, threshold: int, shares: int, *,
          randbytes: Callable[[int], bytes] = secrets.token_bytes) -> List[bytes]:
    """Split ``secret`` into ``shares`` shares, any ``threshold`` of which rebuild it.

    ``randbytes`` is injectable for deterministic tests; it must return uniform
    random bytes.  Returns shares in evaluation-point order (x = 1..shares).
    """
    if not secret:
        raise ValueError("secret must be non-empty")
    if not 1 <= threshold <= shares <= MAX_SHARES:
        raise ValueError(
            f"need 1 <= threshold ({threshold}) <= shares ({shares}) <= {MAX_SHARES}")
    coefficients = [randbytes(len(secret)) for _ in range(threshold - 1)]
    out: List[bytes] = []
    for x in range(1, shares + 1):
        y = bytearray(secret)
        power = 1
        for coeff in coefficients:
            power = _mul(power, x)
            for i in range(len(secret)):
                y[i] ^= _mul(coeff[i], power)
        out.append(bytes([x]) + bytes(y))
    return out


def combine(shares: Sequence[bytes]) -> bytes:
    """Reconstruct the secret from ``threshold`` or more shares (Lagrange at 0)."""
    if not shares:
        raise ValueError("no shares supplied")
    xs = [s[0] for s in shares]
    if any(x == 0 for x in xs):
        raise ValueError("invalid share (zero evaluation point)")
    if len(set(xs)) != len(xs):
        raise ValueError("duplicate share")
    ys = [s[1:] for s in shares]
    length = len(ys[0])
    if length == 0 or any(len(y) != length for y in ys):
        raise ValueError("share length mismatch")

    secret = bytearray(length)
    for i, xi in enumerate(xs):
        numerator = 1
        denominator = 1
        for j, xj in enumerate(xs):
            if j == i:
                continue
            numerator = _mul(numerator, xj)
            denominator = _mul(denominator, xj ^ xi)
        factor = _div(numerator, denominator)
        for k in range(length):
            secret[k] ^= _mul(ys[i][k], factor)
    return bytes(secret)
