"""Typed feature snapshots that the inline tier scores from."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FeatureSnapshot:
    """Common envelope for all feature snapshots."""

    entity_id: str
    last_score: float | None = None
    last_score_at_ms: int | None = None
    raw_bins: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NumberFeatures:
    """Features for a Number entity (CLAUDE.md §6.4)."""

    msisdn: str
    velocity_1m: int = 0
    velocity_5m: int = 0
    velocity_1h: int = 0
    fanout_1h: int = 0
    imei_count: int = 0
    geo_entropy: float = 0.0
    sms_freq_1h: int = 0
    sms_template_top: str | None = None
    # True when the most recent SMS attributable to this MSISDN as the
    # *sender* arrived with rcs_verified=True. Populated by stream-features
    # from sms.events.v1. Brain-behavioural exempts these from IMEI-churn
    # signals: legitimate businesses rotate SMS-routing infrastructure.
    rcs_verified_recent: bool = False
    last_score: float | None = None
    last_score_at_ms: int | None = None

    @classmethod
    def from_bins(cls, msisdn: str, bins: dict[str, Any]) -> NumberFeatures:
        return cls(
            msisdn=msisdn,
            velocity_1m=int(bins.get("vel_1m", 0)),
            velocity_5m=int(bins.get("vel_5m", 0)),
            velocity_1h=int(bins.get("vel_1h", 0)),
            fanout_1h=int(bins.get("fanout_1h", 0)),
            imei_count=int(bins.get("imei_count", 0)),
            geo_entropy=float(bins.get("geo_entropy", 0.0)),
            sms_freq_1h=int(bins.get("sms_freq_1h", 0)),
            sms_template_top=bins.get("smshash_top"),
            rcs_verified_recent=bool(bins.get("rcs_verified", False)),
            last_score=bins.get("last_score"),
            last_score_at_ms=bins.get("last_score_at"),
        )

    def to_bins(self) -> dict[str, Any]:
        return {
            "vel_1m": self.velocity_1m,
            "vel_5m": self.velocity_5m,
            "vel_1h": self.velocity_1h,
            "fanout_1h": self.fanout_1h,
            "imei_count": self.imei_count,
            "geo_entropy": self.geo_entropy,
            "sms_freq_1h": self.sms_freq_1h,
            "smshash_top": self.sms_template_top,
            "rcs_verified": self.rcs_verified_recent,
            "last_score": self.last_score,
            "last_score_at": self.last_score_at_ms,
        }


@dataclass(frozen=True)
class WalletFeatures:
    wallet_id: str
    txn_velocity_1h: int = 0
    counterparty_diversity_24h: int = 0
    value_p95_24h: float = 0.0
    last_score: float | None = None
    last_score_at_ms: int | None = None

    @classmethod
    def from_bins(cls, wallet_id: str, bins: dict[str, Any]) -> WalletFeatures:
        return cls(
            wallet_id=wallet_id,
            txn_velocity_1h=int(bins.get("txn_vel_1h", 0)),
            counterparty_diversity_24h=int(bins.get("cp_div_24h", 0)),
            value_p95_24h=float(bins.get("val_p95_24h", 0.0)),
            last_score=bins.get("last_score"),
            last_score_at_ms=bins.get("last_score_at"),
        )

    def to_bins(self) -> dict[str, Any]:
        return {
            "txn_vel_1h": self.txn_velocity_1h,
            "cp_div_24h": self.counterparty_diversity_24h,
            "val_p95_24h": self.value_p95_24h,
            "last_score": self.last_score,
            "last_score_at": self.last_score_at_ms,
        }
