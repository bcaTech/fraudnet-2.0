"""Domain + IP normalisation.

Domains are lowercased, the trailing dot is stripped, and IDN labels
are decoded to A-label (punycode) so that visually-identical homoglyph
attacks collide on the same canonical key. This matters for the
cross-domain join with brain-content's blocklist, which stores
A-labels.

IPs are validated and emitted as canonical strings (IPv4 dot-decimal,
IPv6 lowercase compressed).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalDomain:
    fqdn: str        # lowercase, no trailing dot, A-label encoded
    registrable: str  # eTLD+1 best-effort (no PSL — kept simple)


def canonicalise_domain(domain: str) -> CanonicalDomain:
    if not domain:
        raise ValueError("empty domain")
    d = domain.strip().rstrip(".").lower()
    if not d or len(d) > 253:
        raise ValueError(f"invalid domain length: {domain!r}")
    # IDN → A-label per label, then re-join. ascii() raises UnicodeError on
    # malformed input.
    try:
        labels = [_to_ascii_label(label) for label in d.split(".")]
    except UnicodeError as exc:
        raise ValueError(f"invalid IDN label in {domain!r}") from exc
    if any(not label for label in labels):
        raise ValueError(f"empty label in {domain!r}")
    fqdn = ".".join(labels)
    # Naive eTLD+1 — no Public Suffix List dependency. brain-content owns
    # the precise version when it needs it; here we only need a stable key
    # for joining with low cardinality.
    if len(labels) >= 2:
        registrable = ".".join(labels[-2:])
    else:
        registrable = fqdn
    return CanonicalDomain(fqdn=fqdn, registrable=registrable)


def _to_ascii_label(label: str) -> str:
    if label.isascii():
        return label
    return label.encode("idna").decode("ascii")


def canonicalise_ip(ip: str) -> str:
    """Validate and canonicalise an IP. Raises ValueError on malformed input."""
    return str(ipaddress.ip_address(ip.strip()))
