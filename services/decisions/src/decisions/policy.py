"""YAML-driven decision policy.

A policy file is a list of rules. The first rule whose `match` predicates
all hold against the input wins; if no rule matches, the `default` block
produces a Tier-3 investigation queue entry.

Match predicates supported:
  - `signal_kind: <str>` — exact match
  - `motif: <str>` — exact match (motif events only)
  - `severity_in: [list]` — severity ∈ list
  - `score_gte: <float>` — score.value >= float
  - `subject_kind: <str>` — exact match (number / wallet / etc.)

Effect fields:
  - `action: <str>` — action identifier (e.g. 'volte.tag_suspected_spam')
  - `tier: tier1 | tier2 | tier3`
  - `suppression_window_s: <int>` — TTL on the signal's suppression_key

Policies are versioned; the policy_id and policy_version are stamped on
every emitted DecisionDispatchedV1.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from fraudnet.schemas.events import MotifDetectedV1
from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import LatencyTier, Severity


@dataclass(frozen=True)
class _Match:
    signal_kind: str | None = None
    motif: str | None = None
    severity_in: tuple[Severity, ...] | None = None
    score_gte: float | None = None
    subject_kind: str | None = None

    def matches_signal(self, sig: SignalEventV1) -> bool:
        if self.motif is not None:
            return False
        if self.signal_kind is not None and sig.signal_kind != self.signal_kind:
            return False
        if self.severity_in is not None and sig.severity not in self.severity_in:
            return False
        if self.score_gte is not None and sig.score.value < self.score_gte:
            return False
        if self.subject_kind is not None and sig.subject.kind.value != self.subject_kind:
            return False
        return True

    def matches_motif(self, m: MotifDetectedV1) -> bool:
        if self.signal_kind is not None:
            return False
        if self.motif is not None and m.motif != self.motif:
            return False
        if self.score_gte is not None and (m.score is None or m.score.value < self.score_gte):
            return False
        return True


@dataclass(frozen=True)
class Rule:
    id: str
    match: _Match
    action: str
    tier: LatencyTier
    suppression_window_s: int = 0


@dataclass(frozen=True)
class Default:
    action: str = "investigation.queue"
    tier: LatencyTier = LatencyTier.TIER3_INVESTIGATION
    suppression_window_s: int = 0


@dataclass(frozen=True)
class Policy:
    id: str
    version: str
    rules: tuple[Rule, ...]
    default: Default = field(default_factory=Default)

    @classmethod
    def load(cls, path: Path) -> "Policy":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "Policy":
        rules: list[Rule] = []
        for r in raw.get("rules", []) or []:  # type: ignore[arg-type]
            assert isinstance(r, dict)
            m = r.get("match", {}) or {}
            sev = m.get("severity_in")
            severity_in = tuple(Severity(s) for s in sev) if sev else None
            rules.append(
                Rule(
                    id=str(r["id"]),
                    match=_Match(
                        signal_kind=m.get("signal_kind"),
                        motif=m.get("motif"),
                        severity_in=severity_in,
                        score_gte=float(m["score_gte"]) if m.get("score_gte") is not None else None,
                        subject_kind=m.get("subject_kind"),
                    ),
                    action=str(r["action"]),
                    tier=LatencyTier(r["tier"]),
                    suppression_window_s=int(r.get("suppression_window_s", 0)),
                )
            )
        d = raw.get("default", {}) or {}
        default = Default(
            action=str(d.get("action", "investigation.queue")),
            tier=LatencyTier(d.get("tier", "tier3")),
            suppression_window_s=int(d.get("suppression_window_s", 0)),
        )
        version = str(raw.get("version", "0"))
        policy_id = str(raw.get("id", "default"))
        return cls(id=policy_id, version=version, rules=tuple(rules), default=default)

    def fingerprint(self) -> str:
        """Stable hash over the loaded rules — useful for cache busting."""
        text = "\n".join(
            f"{r.id}:{r.action}:{r.tier.value}:{r.suppression_window_s}" for r in self.rules
        )
        return hashlib.sha256(text.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class DispatchOutcome:
    rule_id: str
    action: str
    tier: LatencyTier
    suppression_window_s: int


def evaluate_signal(policy: Policy, sig: SignalEventV1) -> DispatchOutcome:
    for rule in policy.rules:
        if rule.match.matches_signal(sig):
            return DispatchOutcome(
                rule_id=rule.id,
                action=rule.action,
                tier=rule.tier,
                suppression_window_s=rule.suppression_window_s,
            )
    return DispatchOutcome(
        rule_id="__default__",
        action=policy.default.action,
        tier=policy.default.tier,
        suppression_window_s=policy.default.suppression_window_s,
    )


def evaluate_motif(policy: Policy, m: MotifDetectedV1) -> DispatchOutcome:
    for rule in policy.rules:
        if rule.match.matches_motif(m):
            return DispatchOutcome(
                rule_id=rule.id,
                action=rule.action,
                tier=rule.tier,
                suppression_window_s=rule.suppression_window_s,
            )
    return DispatchOutcome(
        rule_id="__default__",
        action=policy.default.action,
        tier=policy.default.tier,
        suppression_window_s=policy.default.suppression_window_s,
    )


def load_all(directory: Path) -> Policy:
    """Load and merge YAML policies in a directory.

    Phase 1 ships one default policy. Phase 2+ allows tenant overlays.
    """
    files = sorted(directory.glob("*.yaml"))
    if not files:
        raise FileNotFoundError(f"no policy YAML found under {directory}")
    return Policy.load(files[0])


def discover_default_policy() -> Policy:
    """Locate the bundled default policy. Used at service startup."""
    candidates: Iterable[Path] = (
        Path(__file__).resolve().parent.parent / "policies",  # source layout
        Path(__file__).resolve().parent / "policies",          # installed wheel
    )
    for c in candidates:
        if c.exists():
            return load_all(c)
    raise FileNotFoundError("decisions/policies directory not found")
