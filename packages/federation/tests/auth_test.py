"""HMAC signing — produces, verifies, rejects expired and tampered."""

from __future__ import annotations

from fraudnet.federation.auth import sign_request, verify_signature


def test_sign_then_verify_round_trip() -> None:
    body = b'{"identifier_hashes":["abc"]}'
    headers = sign_request(
        secret="shh", method="POST", path="/federation/v1/flags/lookup", body=body, ts=1_700_000_000
    )
    ok = verify_signature(
        secret="shh",
        method="POST",
        path="/federation/v1/flags/lookup",
        body=body,
        timestamp=headers["X-Federation-Timestamp"],
        signature=headers["X-Federation-Signature"],
        now=1_700_000_001,
    )
    assert ok is True


def test_tampered_body_fails() -> None:
    body = b'{"identifier_hashes":["abc"]}'
    headers = sign_request(secret="shh", method="POST", path="/x", body=body, ts=1)
    ok = verify_signature(
        secret="shh",
        method="POST",
        path="/x",
        body=body + b"_tampered",
        timestamp=headers["X-Federation-Timestamp"],
        signature=headers["X-Federation-Signature"],
        now=2,
    )
    assert ok is False


def test_expired_timestamp_fails() -> None:
    body = b"{}"
    headers = sign_request(secret="shh", method="POST", path="/x", body=body, ts=1_000)
    ok = verify_signature(
        secret="shh",
        method="POST",
        path="/x",
        body=body,
        timestamp=headers["X-Federation-Timestamp"],
        signature=headers["X-Federation-Signature"],
        now=1_000 + 600,  # 10 min after; default tolerance is 5 min
    )
    assert ok is False


def test_wrong_secret_fails() -> None:
    body = b"{}"
    headers = sign_request(secret="shh", method="POST", path="/x", body=body, ts=1)
    ok = verify_signature(
        secret="wrong",
        method="POST",
        path="/x",
        body=body,
        timestamp=headers["X-Federation-Timestamp"],
        signature=headers["X-Federation-Signature"],
        now=1,
    )
    assert ok is False


def test_missing_headers_fail_closed() -> None:
    assert (
        verify_signature(
            secret="shh",
            method="POST",
            path="/x",
            body=b"{}",
            timestamp=None,
            signature=None,
        )
        is False
    )
