"""JWT validation against the Keycloak issuer.

JWKS is fetched once and cached with a TTL. The cache is reentrant across
async tasks. Production deployment fronts Keycloak with a static IdP URL;
the issuer is a strict equality check, not a substring match.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from fraudnet.auth.principal import Principal, Role
from fraudnet.obs import get_logger
from fraudnet.schemas.errors import AuthError

_log = get_logger("fraudnet.auth.token")


class TokenError(AuthError):
    """Token failed validation."""


@dataclass(frozen=True)
class TokenValidatorConfig:
    issuer: str
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...] = ("RS256",)
    leeway_s: int = 30
    jwks_ttl_s: int = 3600


class JwksCache:
    """JWKS cache with TTL, shared across requests."""

    def __init__(self, jwks_url: str, ttl_s: int = 3600) -> None:
        self._jwks_url = jwks_url
        self._ttl = ttl_s
        self._client: PyJWKClient | None = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> PyJWKClient:
        now = time.time()
        if self._client is not None and (now - self._fetched_at) < self._ttl:
            return self._client
        async with self._lock:
            if self._client is not None and (time.time() - self._fetched_at) < self._ttl:
                return self._client
            # PyJWKClient is sync — run in default executor to avoid blocking.
            loop = asyncio.get_running_loop()
            client = await loop.run_in_executor(None, PyJWKClient, self._jwks_url)
            self._client = client
            self._fetched_at = time.time()
            return client


class TokenValidator:
    def __init__(self, config: TokenValidatorConfig) -> None:
        self._cfg = config
        self._jwks = JwksCache(config.jwks_url, ttl_s=config.jwks_ttl_s)

    async def decode(self, token: str) -> dict[str, Any]:
        client = await self._jwks.get()
        try:
            signing_key = client.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError as exc:
            raise TokenError(f"signing key not found: {exc}") from exc

        try:
            return jwt.decode(
                token,
                signing_key,
                algorithms=list(self._cfg.algorithms),
                audience=self._cfg.audience,
                issuer=self._cfg.issuer,
                leeway=self._cfg.leeway_s,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenError(f"invalid token: {exc}") from exc


def decode_token(token: str, validator: TokenValidator) -> dict[str, Any]:
    """Async-friendly façade for non-FastAPI call sites."""
    return asyncio.run(validator.decode(token))


def extract_principal(claims: dict[str, Any]) -> Principal:
    """Map a Keycloak token's claims onto a Principal.

    Keycloak places realm roles under `realm_access.roles`. We trust only
    those, not client-roles, to avoid client-id confusion.
    """
    raw_roles = claims.get("realm_access", {}).get("roles", []) or []
    roles: set[Role] = set()
    for r in raw_roles:
        try:
            roles.add(Role(r))
        except ValueError:
            # Unknown role names are silently dropped; the lint surface is the
            # set of known roles in principal.Role.
            continue
    actor_kind = "service" if Role.SERVICE in roles else "user"
    step_up_at_ms = claims.get("step_up_at_ms")
    return Principal(
        subject=str(claims.get("sub", "")),
        actor_kind=actor_kind,
        roles=frozenset(roles),
        tenant_id=str(claims.get("tenant_id", "mtn-ghana")),
        step_up_at_ms=int(step_up_at_ms) if isinstance(step_up_at_ms, (int, float)) else None,
        claims={k: v for k, v in claims.items() if k != "realm_access"},
    )
