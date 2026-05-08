"""OTP issuance + verification.

Per DECISIONS.md D-005, the OTP delivery is gated on the security team's
OTP service. Phase 1 ships an in-memory adapter that:
  - issues a deterministic code (123456 in dev) and stores it in Redis
    (or in-memory) with a TTL.
  - logs that the OTP "would have been delivered" via SMS.

The HttpOtpAdapter implementation calls the production OTP service when
its URL is configured.
"""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from fraudnet.obs import counter, get_logger

_log = get_logger("api_customer.otp")

_OTP_REQUESTED = counter(
    "api_customer_otp_requested_total",
    "OTP requests received.",
)
_OTP_VERIFIED = counter(
    "api_customer_otp_verified_total",
    "OTP verifications.",
    labelnames=("outcome",),
)


@dataclass(frozen=True)
class OtpStatus:
    delivered: bool


class OtpAdapter(ABC):
    """Pluggable OTP backend. Phase 1 uses InMemoryOtpAdapter for dev and
    HttpOtpAdapter when wired to the security-team OTP service."""

    @abstractmethod
    async def request(self, msisdn: str) -> OtpStatus: ...

    @abstractmethod
    async def verify(self, msisdn: str, code: str) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class InMemoryOtpAdapter(OtpAdapter):
    """Dev / test OTP adapter. Returns a deterministic code (123456) for any
    msisdn. The real adapter is HttpOtpAdapter; this exists so unit tests
    don't need an external service.
    """

    DEV_CODE = "123456"

    def __init__(self) -> None:
        self._issued: set[str] = set()

    async def request(self, msisdn: str) -> OtpStatus:
        _OTP_REQUESTED.inc()
        self._issued.add(msisdn)
        _log.info("otp.dev_issued", code=self.DEV_CODE)
        return OtpStatus(delivered=True)

    async def verify(self, msisdn: str, code: str) -> bool:
        if msisdn not in self._issued:
            _OTP_VERIFIED.labels(outcome="not_issued").inc()
            return False
        ok = code == self.DEV_CODE
        _OTP_VERIFIED.labels(outcome="ok" if ok else "wrong_code").inc()
        if ok:
            self._issued.discard(msisdn)  # one-shot
        return ok

    async def close(self) -> None:
        return None


class RedisOtpAdapter(OtpAdapter):
    """Production-ready Redis-backed OTP. Code generated locally and pushed
    to the OTP delivery service (HttpOtpAdapter does the push). Lookup by
    MSISDN with TTL.
    """

    def __init__(self, *, url: str, ttl_s: int = 300, namespace: str = "customer:otp") -> None:
        import redis.asyncio as redis_async

        self._redis = redis_async.from_url(url, decode_responses=True)
        self._ns = namespace
        self._ttl = ttl_s

    @staticmethod
    def _new_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    async def request(self, msisdn: str) -> OtpStatus:
        _OTP_REQUESTED.inc()
        code = self._new_code()
        await self._redis.set(f"{self._ns}:{msisdn}", code, ex=self._ttl)
        _log.info("otp.issued")
        return OtpStatus(delivered=True)

    async def verify(self, msisdn: str, code: str) -> bool:
        stored = await self._redis.get(f"{self._ns}:{msisdn}")
        if stored is None:
            _OTP_VERIFIED.labels(outcome="not_issued").inc()
            return False
        ok = stored == code
        if ok:
            await self._redis.delete(f"{self._ns}:{msisdn}")  # one-shot
        _OTP_VERIFIED.labels(outcome="ok" if ok else "wrong_code").inc()
        return ok

    async def close(self) -> None:
        await self._redis.close()


class HttpOtpAdapter(OtpAdapter):
    """Production OTP adapter. Posts to the security-team OTP service for
    SMS delivery and verification. Codes are stored on the OTP service side;
    we only proxy."""

    def __init__(self, *, url: str, token: str, timeout_s: float = 2.0) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout_s

    async def request(self, msisdn: str) -> OtpStatus:
        _OTP_REQUESTED.inc()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                f"{self._url}/request", json={"msisdn": msisdn}, headers=self._headers
            )
            return OtpStatus(delivered=r.status_code < 400)

    async def verify(self, msisdn: str, code: str) -> bool:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                f"{self._url}/verify",
                json={"msisdn": msisdn, "code": code},
                headers=self._headers,
            )
            ok = r.status_code == 200 and bool(r.json().get("verified", False))
        _OTP_VERIFIED.labels(outcome="ok" if ok else "rejected").inc()
        return ok

    async def close(self) -> None:
        return None
