"""Sliding-window state per agent.

Each agent keeps a deque of recent transactions trimmed to the longest
detector window. Detectors take a snapshot view of the deque at fire time;
no detector mutates state.

Phase 1: in-process. Restarting the service loses state (a few seconds of
detection coverage). Phase 2 backs this with Redis if scale demands it.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentTxn:
    """One MoMo event seen from an agent's perspective."""

    txn_id: str
    kind: str                # 'cash_in' | 'cash_out' | 'p2p_transfer' | ...
    counterparty_kind: str
    counterparty_id: str | None
    amount_minor: int
    ts_ms: int
    channel: str | None = None
    device_id: str | None = None  # only set when stream-graph attached one


class AgentStateStore:
    """Per-agent rolling window of recent transactions.

    `max_window_s` is the longest detector window (e.g. 24h). Trims on
    every append.
    """

    def __init__(self, *, max_window_s: int = 24 * 3600, max_per_agent: int = 5_000) -> None:
        self._max_window_ms = max_window_s * 1000
        self._max_per_agent = max_per_agent
        self._by_agent: dict[str, deque[AgentTxn]] = defaultdict(deque)

    def append(self, agent_id: str, txn: AgentTxn) -> None:
        dq = self._by_agent[agent_id]
        dq.append(txn)
        cutoff = txn.ts_ms - self._max_window_ms
        while dq and dq[0].ts_ms < cutoff:
            dq.popleft()
        # Hard cap on memory — extreme volume agents are themselves
        # suspicious; the float_manipulation detector picks them up.
        while len(dq) > self._max_per_agent:
            dq.popleft()

    def view(
        self, agent_id: str, *, window_s: int, now_ms: int
    ) -> list[AgentTxn]:
        dq = self._by_agent.get(agent_id)
        if not dq:
            return []
        cutoff = now_ms - window_s * 1000
        return [t for t in dq if t.ts_ms >= cutoff]

    def all_agents(self) -> list[str]:
        return list(self._by_agent)

    def agent_txn_count(self, agent_id: str) -> int:
        return len(self._by_agent.get(agent_id, ()))
