"""Lag-aware consumer health check.

CLAUDE.md §5.1: "a service is unhealthy if its consumer lag exceeds threshold."
This is what services compose into their /health/live and /health/ready
endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from confluent_kafka import Consumer, TopicPartition

from fraudnet.kafka.config import KafkaSettings


@dataclass(frozen=True)
class LagStatus:
    state: Literal["healthy", "degraded", "unhealthy"]
    total_lag: int
    per_partition: dict[int, int]
    threshold_warn: int
    threshold_critical: int
    detail: str


class ConsumerLagProbe:
    """Reports consumer-group lag against configured thresholds.

    Cheap to call; safe to wire into a /health endpoint that's hit every
    second by Kubernetes.
    """

    def __init__(
        self,
        *,
        consumer: Consumer,
        topic: str,
        settings: KafkaSettings,
    ) -> None:
        self._consumer = consumer
        self._topic = topic
        self._warn = settings.lag_warn_threshold
        self._critical = settings.lag_critical_threshold

    def check(self) -> LagStatus:
        try:
            assigned = self._consumer.assignment()
        except Exception as exc:  # noqa: BLE001 — broad catch for health-probe robustness
            return LagStatus(
                state="unhealthy",
                total_lag=-1,
                per_partition={},
                threshold_warn=self._warn,
                threshold_critical=self._critical,
                detail=f"assignment failed: {exc}",
            )

        if not assigned:
            return LagStatus(
                state="degraded",
                total_lag=0,
                per_partition={},
                threshold_warn=self._warn,
                threshold_critical=self._critical,
                detail="no partitions assigned (rebalancing or idle)",
            )

        per_partition: dict[int, int] = {}
        total = 0
        for tp in assigned:
            committed = self._consumer.committed([tp])[0]
            current = committed.offset if committed.offset >= 0 else 0
            try:
                _, high = self._consumer.get_watermark_offsets(
                    TopicPartition(tp.topic, tp.partition),
                    timeout=2.0,
                    cached=False,
                )
            except Exception:  # noqa: BLE001 — broad catch for health-probe robustness
                high = current
            lag = max(0, high - current)
            per_partition[tp.partition] = lag
            total += lag

        if total >= self._critical:
            state: Literal["healthy", "degraded", "unhealthy"] = "unhealthy"
        elif total >= self._warn:
            state = "degraded"
        else:
            state = "healthy"
        return LagStatus(
            state=state,
            total_lag=total,
            per_partition=per_partition,
            threshold_warn=self._warn,
            threshold_critical=self._critical,
            detail=f"{state}: total_lag={total}",
        )
