"""Receiver trust store: multi-anchor set with overlap-based rotation."""

import pytest

pki = pytest.importorskip("openatsc3_pki")

from atsc3lib import security
from openatsc3_pki import ca


def _root(tmp_path, name, common_name=None):
    a = ca.CertificateAuthority(str(tmp_path / name))
    a.init_root(**({"common_name": common_name} if common_name else {}))
    return a


def test_truststore_dedupes_and_supports_rotation(tmp_path):
    old = _root(tmp_path, "a").load_root()
    new = _root(tmp_path, "b", "OpenATSC3 Root CA (new)").load_root()

    store = security.TrustStore([old])
    assert len(store) == 1
    store.add(old)                 # idempotent: no duplicate
    assert len(store) == 1

    store.add(new)                 # overlap window: old and new both trusted
    assert len(store) == 2
    assert old in store and new in store

    store.discard(old)             # drop the old anchor once rotation completes
    assert len(store) == 1
    assert old not in store
    assert list(store) == [new]


def test_truststore_is_a_sequence(tmp_path):
    root = _root(tmp_path, "a").load_root()
    store = security.TrustStore([root])
    assert store[0].der() == root.der()
    assert len(store.anchors) == 1
    assert store.keys() == (root.der(),)
    assert repr(store) == "TrustStore(1 anchor)"


def test_load_trust_store_from_paths(tmp_path):
    a = _root(tmp_path, "a")
    store = security.load_trust_store([a.root_cert_path])
    assert len(store) == 1
