"""Hashing properties: deterministic, kind-sensitive, salt-sensitive,
and produces fixed-length hex.

These tests are the wire-format guarantee for the federation protocol —
breaking any of them is a breaking change to every peer's deployed code.
"""

from __future__ import annotations

from fraudnet.federation import (
    DEFAULT_SALT,
    anonymize_device_fingerprint,
    hash_identifier,
    hash_identifier_with_salt,
)


def test_hash_is_deterministic() -> None:
    a = hash_identifier("+233200000001", kind="msisdn", salt=DEFAULT_SALT)
    b = hash_identifier("+233200000001", kind="msisdn", salt=DEFAULT_SALT)
    assert a == b


def test_hash_is_kind_sensitive() -> None:
    """Same plaintext, different kind → different hash. Defends against a
    confused-deputy lookup that asks 'is this MSISDN a wallet hash?'."""
    n = hash_identifier("12345", kind="msisdn", salt=DEFAULT_SALT)
    w = hash_identifier("12345", kind="wallet", salt=DEFAULT_SALT)
    assert n != w


def test_hash_is_salt_sensitive() -> None:
    """Different salt → different hash. Salt rotation must invalidate."""
    a = hash_identifier_with_salt("+233200000001", kind="msisdn", salt="v1")
    b = hash_identifier_with_salt("+233200000001", kind="msisdn", salt="v2")
    assert a != b


def test_hash_is_64_char_hex() -> None:
    h = hash_identifier("anything", kind="msisdn", salt=DEFAULT_SALT)
    assert len(h) == 64
    int(h, 16)  # parses as hex


def test_device_fingerprint_is_truncated() -> None:
    """Truncation is intentional — see docstring on `anonymize_device_fingerprint`."""
    full_hash = hash_identifier("imei12345", kind="imei", salt=DEFAULT_SALT)
    fp = anonymize_device_fingerprint("imei12345", salt=DEFAULT_SALT)
    assert len(fp) == 16
    assert full_hash.startswith(fp)


def test_pii_does_not_appear_in_hash() -> None:
    """Sanity: the plaintext is unrecoverable from the hex digest."""
    msisdn = "+233200000001"
    h = hash_identifier(msisdn, kind="msisdn", salt=DEFAULT_SALT)
    assert msisdn not in h
    assert "200000001" not in h
