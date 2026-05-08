"""Per-agent risk profile + ranking.

Every detector pass updates the agent's running profile. The API
queries return the latest snapshot per agent.

Phase 1: profiles live in-process, refreshed on each detector run.
Phase 2 backs with Postgres so profiles survive restarts and can be
joined into NOC queries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from brain_agent_fraud.detectors import Detection, composite_agent_score


@dataclass
class AgentProfile:
    agent_id: str
    composite_score: float = 0.0
    last_seen_ts_ms: int = 0
    pattern_scores: dict[str, float] = field(default_factory=dict)
    pattern_evidence: dict[str, dict[str, str | int | float | bool]] = field(
        default_factory=dict
    )
    txn_count: int = 0


class ProfileStore:
    def __init__(self, *, decay_per_hour: float = 0.1) -> None:
        # Decay so a quiet agent's score drifts back to zero. Defaults
        # to 10% per hour (full decay over ~10 hours of inactivity).
        self._decay_per_hour = decay_per_hour
        self._profiles: dict[str, AgentProfile] = {}

    def update(
        self,
        *,
        agent_id: str,
        detections: list[Detection],
        txn_count: int,
        now_ms: int | None = None,
    ) -> AgentProfile:
        now_ms = now_ms or int(time.time() * 1000)
        profile = self._profiles.get(agent_id)
        if profile is None:
            profile = AgentProfile(agent_id=agent_id)
            self._profiles[agent_id] = profile

        if profile.last_seen_ts_ms > 0:
            hours = (now_ms - profile.last_seen_ts_ms) / (3600 * 1000)
            decay = max(0.0, 1 - hours * self._decay_per_hour)
            profile.composite_score *= decay
            for k in list(profile.pattern_scores):
                profile.pattern_scores[k] *= decay

        for d in detections:
            # Take the higher of decayed-old vs new — patterns shouldn't
            # trip backwards on a single quiet window.
            profile.pattern_scores[d.signal_kind] = max(
                profile.pattern_scores.get(d.signal_kind, 0.0), d.score
            )
            profile.pattern_evidence[d.signal_kind] = dict(d.evidence)
        profile.composite_score = max(
            profile.composite_score, composite_agent_score(detections)
        )
        profile.last_seen_ts_ms = now_ms
        profile.txn_count += txn_count
        return profile

    def get(self, agent_id: str) -> AgentProfile | None:
        return self._profiles.get(agent_id)

    def ranking(
        self,
        *,
        limit: int = 50,
        min_score: float = 0.5,
    ) -> list[AgentProfile]:
        candidates = [p for p in self._profiles.values() if p.composite_score >= min_score]
        candidates.sort(key=lambda p: p.composite_score, reverse=True)
        return candidates[:limit]

    def commission_anomalies(self, *, limit: int = 50) -> list[AgentProfile]:
        """Agents whose composite score is dominated by commission_farming."""
        out = []
        for p in self._profiles.values():
            cf = p.pattern_scores.get("agent.commission_farming", 0.0)
            if cf >= 0.6:
                out.append(p)
        out.sort(
            key=lambda p: p.pattern_scores.get("agent.commission_farming", 0.0),
            reverse=True,
        )
        return out[:limit]
