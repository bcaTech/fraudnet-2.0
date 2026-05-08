"""Per-period corpus the formatters consume.

The corpus is the union of audit events, alerts, decisions, and actions
in the period. The compliance API loads it once and hands it to the
relevant formatter.

Plaintext PII is intentionally absent from the corpus. Subjects are
referenced by their `subject_kind` + an opaque short code (`abc12345`)
so the regulator pack contains stable references but no MSISDN /
wallet_id / IMEI in cleartext. Submissions that legally require
plaintext (e.g. GFIC SAR with subscriber identification) carry a
`needs_review` field that the human reviewer fills in via a separate
authenticated path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def short_subject(subject_kind: str | None, subject_id: str | None) -> str:
    """Stable 8-char hex token derived from (kind, id). Used in regulator
    packs in lieu of plaintext identifiers."""
    if not subject_id:
        return f"{subject_kind or 'unknown'}:none"
    digest = hashlib.sha256(f"{subject_kind}|{subject_id}".encode()).hexdigest()[:8]
    return f"{subject_kind or 'subj'}:{digest}"


@dataclass(frozen=True)
class PeriodCorpus:
    period_start: datetime
    period_end: datetime
    tenant_id: str
    audit_events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    alerts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    decisions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    actions_taken: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    rings: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def corpus_summary(c: PeriodCorpus) -> dict[str, int]:
    by_severity: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for a in c.alerts:
        sev = a.get("severity") or "unknown"
        by_severity[sev] = by_severity.get(sev, 0) + 1
    for d in c.actions_taken:
        kind = d.get("action_kind") or "unknown"
        by_action[kind] = by_action.get(kind, 0) + 1
    return {
        "audit_events": len(c.audit_events),
        "alerts": len(c.alerts),
        "decisions": len(c.decisions),
        "actions": len(c.actions_taken),
        "rings": len(c.rings),
        **{f"sev_{k}": v for k, v in by_severity.items()},
        **{f"action_{k}": v for k, v in by_action.items()},
    }


def closed_alerts(c: PeriodCorpus) -> list[dict[str, Any]]:
    return [a for a in c.alerts if a.get("status") in ("closed", "executed")]


def confirmed_fraud_alerts(c: PeriodCorpus) -> list[dict[str, Any]]:
    """Alerts confirmed as fraud (closed with reason ≠ false_positive)."""
    return [
        a
        for a in c.alerts
        if a.get("status") == "closed" and a.get("status") != "fp"
    ]
