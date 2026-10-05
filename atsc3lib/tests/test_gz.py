"""Tests for bounded gzip decompression and reassembly bounds."""

import gzip

import pytest

from atsc3lib.gz import (
    BoundedDecompressionError,
    GZIP_MAGIC,
    decompress_bounded,
)


class TestBoundedDecompress:
    def test_inflates_gzip(self):
        assert decompress_bounded(gzip.compress(b"hello")) == b"hello"

    def test_passthrough_uncompressed(self):
        assert decompress_bounded(b"plain") == b"plain"

    def test_rejects_over_bound(self):
        blob = gzip.compress(b"x" * 100000)
        with pytest.raises(BoundedDecompressionError):
            decompress_bounded(blob, max_bytes=1024)

    def test_rejects_large_uncompressed(self):
        with pytest.raises(BoundedDecompressionError):
            decompress_bounded(b"y" * 2048, max_bytes=1024)

    def test_magic_constant(self):
        assert gzip.compress(b"")[:2] == GZIP_MAGIC
