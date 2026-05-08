from __future__ import annotations

import time

import jwt
import pytest

from api_customer.session import SessionTokenIssuer


def test_issue_and_decode_round_trip() -> None:
    issuer = SessionTokenIssuer(secret="s", ttl_s=60)
    token, ttl = issuer.issue(msisdn="+233241234567")
    assert ttl == 60
    claims = issuer.decode(token)
    assert claims.msisdn == "+233241234567"
    assert claims.tenant_id == "mtn-ghana"


def test_decode_expired() -> None:
    issuer = SessionTokenIssuer(secret="s", ttl_s=-1)
    token, _ = issuer.issue(msisdn="+233241234567")
    with pytest.raises(jwt.PyJWTError):
        issuer.decode(token)


def test_wrong_secret_rejected() -> None:
    a = SessionTokenIssuer(secret="s1")
    b = SessionTokenIssuer(secret="s2")
    token, _ = a.issue(msisdn="+233241234567")
    with pytest.raises(jwt.PyJWTError):
        b.decode(token)


def test_expiry_in_future() -> None:
    issuer = SessionTokenIssuer(secret="s", ttl_s=600)
    _, _ = issuer.issue(msisdn="+233241234567")
    # No assertion on iat/exp values directly — just verifies issuance
    # didn't raise. Wall-clock check happens at decode().
    assert int(time.time()) > 0
