"""Session JWT for customer self-service.

HS256 in Phase 1 (DECISIONS.md D-005). Production swaps to RS256 once the
security team's KMS provisioning lands; the validator interface stays the
same.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt


@dataclass(frozen=True)
class SessionClaims:
    msisdn: str
    tenant_id: str
    exp_ms: int


class SessionTokenIssuer:
    def __init__(self, *, secret: str, ttl_s: int = 1800, issuer: str = "api-customer") -> None:
        self._secret = secret
        self._ttl = ttl_s
        self._issuer = issuer

    def issue(self, *, msisdn: str, tenant_id: str = "mtn-ghana") -> tuple[str, int]:
        now = int(time.time())
        exp = now + self._ttl
        token = jwt.encode(
            {
                "iss": self._issuer,
                "iat": now,
                "exp": exp,
                "msisdn": msisdn,
                "tenant_id": tenant_id,
            },
            self._secret,
            algorithm="HS256",
        )
        return token, self._ttl

    def decode(self, token: str) -> SessionClaims:
        decoded = jwt.decode(
            token,
            self._secret,
            algorithms=["HS256"],
            issuer=self._issuer,
            options={"require": ["exp", "iat", "iss"]},
        )
        return SessionClaims(
            msisdn=str(decoded["msisdn"]),
            tenant_id=str(decoded.get("tenant_id", "mtn-ghana")),
            exp_ms=int(decoded["exp"]) * 1000,
        )
