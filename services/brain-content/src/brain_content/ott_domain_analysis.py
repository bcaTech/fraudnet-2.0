"""OTT-specific domain analysis.

Three detectors targeting OTT fraud patterns brain-content sees on
`data.events.v1` and `sms.events.v1`:

  1. Brand-lookalike detection — domains imitating MTN / MoMo / partner
     banks. Combines (a) brand-keyword substring presence outside the
     legitimate registrable domain and (b) edit-distance to known good
     domains. Designed to catch `mtnmomo-secure.example.com`,
     `m0mo.tld`, `mtn-rewards.tld`, etc.

  2. URL shortener abuse — domains in the well-known short-url-host
     list. Shorteners are not malicious per se but let an attacker hide
     the destination from URL reputation lookups, so we annotate any
     SMS/IPDR landing on one and a downstream signal aggregator decides.

  3. Newly-registered domain (NRD) heuristic — we don't have authoritative
     WHOIS in this layer, so we approximate "newly registered" by
     "first time seen on the wire by FraudNet". A `FirstSeenTracker`
     records first-seen timestamps; a domain is NRD when its first-seen
     is younger than `nrd_window_days`. False positives are accepted —
     downstream gives NRD a low standalone weight.

These detectors are pure-function modules; they compose into the
existing classifier verdict in `classifier.py` and feed the DNS scanner
in `dns_scanner.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import time

# Brand keywords whose presence outside legitimate registrable domains
# is a strong impersonation signal. Lowercase, ASCII; the canonical
# domain at the call site has already been A-label normalised by the
# ingest adapter so homoglyph variants (mοmo, etc.) collapse here too.
_BRAND_KEYWORDS = frozenset({"mtn", "momo", "ecobank", "gcb", "bog"})

# Legitimate registrable domains for the brands we protect. A lookalike
# is one whose registrable IS NOT in this set yet shares a brand keyword
# or has small edit-distance to one of these labels.
_LEGITIMATE = frozenset(
    {
        "mtn.com",
        "mtn.com.gh",
        "mymtn.com.gh",
        "ecobank.com",
        "gcb.com.gh",
        "bog.gov.gh",
    }
)

# Well-known short-URL hosts. Match by exact host or by registrable suffix.
_SHORTENERS = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "is.gd",
        "buff.ly",
        "ow.ly",
        "rb.gy",
        "cutt.ly",
        "shorturl.at",
        "lnkd.in",
        "rebrand.ly",
        "bl.ink",
        "snip.ly",
        "soo.gd",
    }
)

# Default newly-registered window. 30 days matches the threat-intel
# convention and the url-intel signal_entry_ttl_s default.
DEFAULT_NRD_WINDOW_DAYS = 30


@dataclass(frozen=True)
class OttDomainVerdict:
    """Composite verdict; multiple flags may be set on one domain."""

    domain: str
    is_brand_lookalike: bool = False
    lookalike_target: str | None = None
    lookalike_distance: int | None = None
    is_url_shortener: bool = False
    is_newly_registered: bool = False
    nrd_age_seconds: int | None = None

    @property
    def is_suspicious(self) -> bool:
        return (
            self.is_brand_lookalike or self.is_url_shortener or self.is_newly_registered
        )

    def to_evidence(self) -> dict[str, str | int | float | bool]:
        out: dict[str, str | int | float | bool] = {"domain": self.domain}
        if self.is_brand_lookalike:
            out["brand_lookalike"] = True
            if self.lookalike_target:
                out["lookalike_target"] = self.lookalike_target
            if self.lookalike_distance is not None:
                out["lookalike_distance"] = int(self.lookalike_distance)
        if self.is_url_shortener:
            out["url_shortener"] = True
        if self.is_newly_registered:
            out["newly_registered"] = True
            if self.nrd_age_seconds is not None:
                out["nrd_age_seconds"] = int(self.nrd_age_seconds)
        return out


# ---------------------------------------------------------------------------
# Brand lookalike
# ---------------------------------------------------------------------------


def _registrable(domain: str) -> str:
    """Naive eTLD+1: last two labels. Same convention as ingest-data."""
    labels = domain.split(".")
    if len(labels) <= 2:
        return domain
    return ".".join(labels[-2:])


def _levenshtein(a: str, b: str, *, cap: int = 4) -> int:
    """Bounded Levenshtein. Returns cap+1 if strings differ by more than cap.

    Iterative DP, O(min(|a|,|b|)) memory. The cap lets us short-circuit
    when comparing a long fan-out of candidate domains.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        row_min = curr[0]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            row_min = min(row_min, curr[j])
        if row_min > cap:
            return cap + 1
        prev = curr
    return prev[-1]


def detect_brand_lookalike(
    domain: str,
    *,
    legitimate: frozenset[str] = _LEGITIMATE,
    brand_keywords: frozenset[str] = _BRAND_KEYWORDS,
    distance_threshold: int = 2,
) -> tuple[bool, str | None, int | None]:
    """Return (is_lookalike, target, distance).

    A domain is a lookalike when its registrable is not in `legitimate`
    AND either:
      - any brand keyword appears as a substring of any label, or
      - its registrable label edit-distance to any legitimate registrable
        label is <= `distance_threshold` (and > 0 — exact matches are
        legitimate, handled above).
    """
    d = domain.lower().rstrip(".")
    reg = _registrable(d)
    if reg in legitimate:
        return False, None, None

    # Substring brand-keyword check: any label containing a brand keyword
    # while the registrable is not legitimate is the strongest signal.
    for label in d.split("."):
        for kw in brand_keywords:
            if kw in label:
                return True, kw, None

    # Edit-distance against the *root label* of each legitimate domain.
    closest_target: str | None = None
    closest_dist: int = distance_threshold + 1
    for legit in legitimate:
        root = legit.split(".")[0]
        candidate = reg.split(".")[0]
        dist = _levenshtein(candidate, root, cap=distance_threshold)
        if 0 < dist < closest_dist:
            closest_dist = dist
            closest_target = legit
    if closest_target is not None:
        return True, closest_target, closest_dist
    return False, None, None


# ---------------------------------------------------------------------------
# URL shortener
# ---------------------------------------------------------------------------


def is_url_shortener(domain: str, *, shorteners: frozenset[str] = _SHORTENERS) -> bool:
    d = domain.lower().rstrip(".")
    if d in shorteners:
        return True
    for sh in shorteners:
        if d.endswith("." + sh):
            return True
    return False


# ---------------------------------------------------------------------------
# Newly registered domain (NRD) heuristic
# ---------------------------------------------------------------------------


class FirstSeenTracker:
    """Tracks first-seen timestamps for domains observed on the wire.

    Backed by an in-memory dict in Phase 3; production should swap for
    a Redis-backed store so the tracker survives restarts and is shared
    across replicas. The interface stays the same.
    """

    def __init__(self, *, max_entries: int = 200_000) -> None:
        self._first_seen: dict[str, int] = {}
        self._max = max_entries

    def observe(self, domain: str, *, now_ms: int | None = None) -> int:
        """Record (or read) the first-seen timestamp for domain. Returns ms."""
        d = domain.lower().rstrip(".")
        if d in self._first_seen:
            return self._first_seen[d]
        ts = now_ms if now_ms is not None else int(time() * 1000)
        if len(self._first_seen) >= self._max:
            # FIFO eviction: drop the oldest one. Cheap and correct;
            # avoids unbounded growth without an explicit retention pass.
            self._first_seen.pop(next(iter(self._first_seen)))
        self._first_seen[d] = ts
        return ts

    def first_seen_ms(self, domain: str) -> int | None:
        return self._first_seen.get(domain.lower().rstrip("."))


def is_newly_registered(
    domain: str,
    tracker: FirstSeenTracker,
    *,
    window_days: int = DEFAULT_NRD_WINDOW_DAYS,
    now_ms: int | None = None,
) -> tuple[bool, int | None]:
    """Return (is_nrd, age_seconds). Records the domain if first-seen.

    The check is "young first-seen", not "young registration" — we don't
    have WHOIS in this layer. NRD is a soft signal in isolation; combine
    with brand-lookalike or shortener for a strong signal.
    """
    ts = tracker.observe(domain, now_ms=now_ms)
    cur_ms = now_ms if now_ms is not None else int(time() * 1000)
    age_seconds = max(0, (cur_ms - ts) // 1000)
    return age_seconds <= window_days * 24 * 60 * 60, age_seconds


# ---------------------------------------------------------------------------
# Composite analyser
# ---------------------------------------------------------------------------


class OttDomainAnalyser:
    """One-shot analyser that runs all three detectors and returns a verdict.

    Hot-loaded blocklist set is exposed via `suspicious_domains`; the
    stream-features pipeline can subscribe by reference for in-flight
    suspicious-domain attribution.
    """

    def __init__(
        self,
        *,
        legitimate: frozenset[str] = _LEGITIMATE,
        brand_keywords: frozenset[str] = _BRAND_KEYWORDS,
        shorteners: frozenset[str] = _SHORTENERS,
        first_seen_tracker: FirstSeenTracker | None = None,
        nrd_window_days: int = DEFAULT_NRD_WINDOW_DAYS,
        distance_threshold: int = 2,
    ) -> None:
        self._legit = legitimate
        self._brands = brand_keywords
        self._shorteners = shorteners
        self._tracker = first_seen_tracker or FirstSeenTracker()
        self._nrd_days = nrd_window_days
        self._distance = distance_threshold
        # Set of domains the analyser has flagged as suspicious; exposed
        # so stream-features can use it as a hot-loaded blocklist.
        self._suspicious: set[str] = set()

    @property
    def suspicious_domains(self) -> set[str]:
        return self._suspicious

    @property
    def first_seen_tracker(self) -> FirstSeenTracker:
        return self._tracker

    def analyse(self, domain: str, *, now_ms: int | None = None) -> OttDomainVerdict:
        d = domain.lower().rstrip(".")
        is_lookalike, target, dist = detect_brand_lookalike(
            d,
            legitimate=self._legit,
            brand_keywords=self._brands,
            distance_threshold=self._distance,
        )
        is_short = is_url_shortener(d, shorteners=self._shorteners)
        is_nrd, age_s = is_newly_registered(
            d, self._tracker, window_days=self._nrd_days, now_ms=now_ms
        )
        verdict = OttDomainVerdict(
            domain=d,
            is_brand_lookalike=is_lookalike,
            lookalike_target=target,
            lookalike_distance=dist,
            is_url_shortener=is_short,
            is_newly_registered=is_nrd,
            nrd_age_seconds=age_s if is_nrd else None,
        )
        if verdict.is_suspicious:
            # Lookalikes and shorteners are durable signals; NRD alone is
            # too weak to add to the hot blocklist (would noise up the
            # stream-features signal).
            if is_lookalike or is_short:
                self._suspicious.add(d)
        return verdict
