"""HMAC request signing for inter-opco federation calls.

Phase 4 simplification: pre-shared secret per peer pair, HMAC-SHA256 over
`{ts}|{method}|{path}|{body_sha256}`. Tolerates 5 minutes of clock skew.

Replaced by mTLS + SPIFFE workload identity in Phase 4.5 once Group IT
finishes the cross-opco PKI rollout. The interface stays the same: a
header-based signature that the server verifies before invoking the route
handler.
"""

from __future__ import annotations

import hashlib
import hmac
import time

# Maximum permitted age, in seconds, of an inbound signed request. Prevents
# replay attacks beyond the listed window. Must match clock-skew tolerance.
SIGNATURE_MAX_AGE_S = 300


def _body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _signing_string(*, ts: int, method: str, path: str, body: bytes) -> bytes:
    return f"{ts}|{method.upper()}|{path}|{_body_digest(body)}".encode()


def sign_request(
    *,
    secret: str,
    method: str,
    path: str,
    body: bytes,
    ts: int | None = None,
) -> dict[str, str]:
    """Produce headers to attach to an outbound federation request."""
    ts = ts or int(time.time())
    msg = _signing_string(ts=ts, method=method, path=path, body=body)
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return {
        "X-Federation-Timestamp": str(ts),
        "X-Federation-Signature": sig,
    }


def verify_signature(
    *,
    secret: str,
    method: str,
    path: str,
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    now: int | None = None,
    max_age_s: int = SIGNATURE_MAX_AGE_S,
) -> bool:
    """True iff the signature is valid and within the freshness window.

    Constant-time comparison via `hmac.compare_digest`. Missing or malformed
    headers fail closed.
    """
    if not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    now = now or int(time.time())
    if abs(now - ts) > max_age_s:
        return False
    msg = _signing_string(ts=ts, method=method, path=path, body=body)
    expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
