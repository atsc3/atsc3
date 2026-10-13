"""Shamir quorum custody for the root key (3-of-5 and friends)."""

import pytest

from openatsc3_pki import quorum


def test_split_and_combine_round_trip():
    secret = b"the root private key bytes"
    shares = quorum.split(secret, 3, 5)
    assert len(shares) == 5
    assert quorum.combine(shares[:3]) == secret
    assert quorum.combine([shares[0], shares[2], shares[4]]) == secret
    assert quorum.combine(shares) == secret


def test_fewer_than_threshold_reveals_nothing():
    secret = b"0123456789abcdef"
    shares = quorum.split(secret, 3, 5)
    assert quorum.combine(shares[:2]) != secret


def test_share_prefix_is_the_evaluation_point():
    secret = b"\x00" * 8
    shares = quorum.split(secret, 3, 5)
    assert [s[0] for s in shares] == [1, 2, 3, 4, 5]
    assert all(len(s) == len(secret) + 1 for s in shares)


def test_deterministic_with_injected_randomness():
    calls = {"n": 0}

    def fake(count):
        calls["n"] += 1
        return bytes([calls["n"] & 0xFF]) * count

    first = quorum.split(b"seed", 2, 3, randbytes=fake)
    calls["n"] = 0
    second = quorum.split(b"seed", 2, 3, randbytes=fake)
    assert first == second
    assert quorum.combine(first[:2]) == b"seed"


def test_duplicate_and_zero_shares_rejected():
    shares = quorum.split(b"secret", 2, 3)
    with pytest.raises(ValueError):
        quorum.combine([shares[0], shares[0]])
    with pytest.raises(ValueError):
        quorum.combine([b"\x00" + shares[0][1:], shares[1]])


def test_reshare_produces_fresh_shares_for_the_same_secret():
    secret = b"the root private key bytes"
    old = quorum.split(secret, 3, 5)
    new = quorum.reshare(old[:3], 3, 5)
    assert len(new) == 5
    assert new != old
    assert quorum.combine(new[:3]) == secret
    assert quorum.combine([new[0], new[2], new[4]]) == secret


def test_reshare_validates_parameters():
    old = quorum.split(b"secret", 3, 5)
    with pytest.raises(ValueError):
        quorum.reshare(old[:3], 5, 3)


def test_validation():
    with pytest.raises(ValueError):
        quorum.split(b"", 3, 5)
    with pytest.raises(ValueError):
        quorum.split(b"secret", 5, 3)
    with pytest.raises(ValueError):
        quorum.split(b"secret", 0, 5)
    with pytest.raises(ValueError):
        quorum.split(b"secret", 1, quorum.MAX_SHARES + 1)
    with pytest.raises(ValueError):
        quorum.combine([])
