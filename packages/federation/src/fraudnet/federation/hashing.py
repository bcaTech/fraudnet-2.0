"""Hashing primitives for cross-opco identifier exchange.

The canonical wire format for identifiers crossing opco boundaries is a
salted SHA-256 hex digest. The salt is global (rotated by Group IT). All
opcos in the federation share the same salt during a given rotation
window so hashes match across opcos.

Why salt at all when we want matches to work? Two reasons:
  1. A fixed salt prevents rainbow-table attacks against the (relatively
     small) MSISDN keyspace. Without a salt, a SHA-256 of a Ghana MSISDN
     is trivially reversible.
  2. Salt rotation is the kill-switch for a leaked hash corpus.

Device fingerprints are anonymized via a separate one-way reduction (we
only share *that* the device is shared across N numbers; we never share
the IMEI itself).
"""

from __future__ import annotations

import hashlib
import os

# The default dev salt. Production deployments override via the
# FRAUDNET_FEDERATION_SALT environment variable, set by Vault.
DEFAULT_SALT = "fraudnet-federation-v1"


def _resolve_salt(explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    return os.environ.get("FRAUDNET_FEDERATION_SALT", DEFAULT_SALT)


def hash_identifier(value: str, *, kind: str, salt: str | None = None) -> str:
    """Hash an identifier for cross-opco transmission.

    The hash includes the kind so that "+233200000001" hashed as msisdn
    is distinct from the same string hashed as wallet — this is a defence
    against confused-deputy lookups across kinds.

    Args:
        value: The plaintext identifier (e.g. an MSISDN). Must be the
            normalized form — E.164 for numbers, lowercase for wallets,
            etc. Caller is responsible for normalization.
        kind: One of 'msisdn' | 'wallet' | 'imei' | 'url' | 'account'.
        salt: Override the global salt. Tests use this; production should
            not.

    Returns:
        Lowercase hex SHA-256, 64 chars.
    """
    return hash_identifier_with_salt(value, kind=kind, salt=_resolve_salt(salt))


def hash_identifier_with_salt(value: str, *, kind: str, salt: str) -> str:
    """Pure-function variant of `hash_identifier` for callers that need to
    pass an explicit salt (e.g. acceptance during the rotation window)."""
    payload = f"{salt}|{kind}|{value}".encode()
    return hashlib.sha256(payload).hexdigest()


def anonymize_device_fingerprint(imei: str, *, salt: str | None = None) -> str:
    """Anonymize a device fingerprint for federation.

    Stricter than `hash_identifier`: the output is a 16-char prefix of the
    SHA-256, intentionally lossy. This means cross-opco device matches
    have a small false-positive rate (~1 in 2^64 for unrelated devices),
    accepted as the cost of one-way anonymity. The opco that actually owns
    the device retains the full IMEI; only the truncated fingerprint
    travels.
    """
    full = hash_identifier_with_salt(imei, kind="imei", salt=_resolve_salt(salt))
    return full[:16]
