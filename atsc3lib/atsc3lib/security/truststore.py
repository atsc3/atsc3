"""Receiver trust store: a set of root anchors with overlap-based rotation.

A/360 5.4 has a receiver hold a **set** of trusted root certificates in secure
storage, and states the set changes over time (by device code download or other
means, out of A/360's scope).  This models that set for the software receiver.

The verifier checks a chain against **every** anchor at once, so an anchor
rotation is graceful: add the new root while keeping the old one for an overlap
window, distribute/anchor as the device's update path allows, then drop the old
anchor once no live chain needs it.  Anchors are de-duplicated by certificate
identity (SHA-256 of the DER when the anchor exposes ``der()``), so a root loaded
from two paths is stored once.  The store is a :class:`Sequence` of anchors, so
it can be passed straight to :func:`atsc3lib.security.verify_streams`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Iterable, Iterator, Tuple

__all__ = ["TrustStore"]


def _anchor_key(anchor) -> object:
    """A stable identity for an anchor (its DER bytes when available)."""
    der = getattr(anchor, "der", None)
    if callable(der):
        return der()
    return repr(anchor)


class TrustStore(Sequence):
    """An ordered, de-duplicated set of trusted root anchors.

    Implements :class:`collections.abc.Sequence`, so it is directly acceptable
    where a list of anchors is expected.
    """

    def __init__(self, anchors: Iterable = ()) -> None:
        self._by_key = {}
        for anchor in anchors:
            self.add(anchor)

    def add(self, anchor) -> "TrustStore":
        """Add ``anchor`` (idempotent); a re-add does not create a duplicate."""
        self._by_key[_anchor_key(anchor)] = anchor
        return self

    def update(self, anchors: Iterable) -> "TrustStore":
        for anchor in anchors:
            self.add(anchor)
        return self

    def discard(self, anchor) -> "TrustStore":
        """Remove ``anchor`` if present (no error when absent)."""
        self._by_key.pop(_anchor_key(anchor), None)
        return self

    @property
    def anchors(self) -> Tuple:
        """The anchors, in insertion order."""
        return tuple(self._by_key.values())

    def keys(self) -> Tuple:
        """The anchor identities (keys), in insertion order."""
        return tuple(self._by_key)

    def __iter__(self) -> Iterator:
        return iter(self._by_key.values())

    def __len__(self) -> int:
        return len(self._by_key)

    def __getitem__(self, index):
        return list(self._by_key.values())[index]

    def __contains__(self, anchor) -> bool:
        return _anchor_key(anchor) in self._by_key

    def __repr__(self) -> str:
        return f"TrustStore({len(self)} anchor{'s' if len(self) != 1 else ''})"
