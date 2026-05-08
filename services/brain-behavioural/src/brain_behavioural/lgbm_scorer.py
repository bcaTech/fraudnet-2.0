"""LightGBM-backed behavioural scorer.

Loaded from the model registry (`fraudnet.registry.ModelRegistry`). The
artifact is the Booster's text dump produced by `Booster.model_to_string()`,
which makes it portable across LightGBM versions and serialisable as bytes.

Falls back to the Phase-1 HeuristicScorer if the registry has no champion
for the model_id — keeps dev/test paths working without MinIO.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any

from fraudnet.features.snapshot import NumberFeatures, WalletFeatures
from fraudnet.obs import get_logger
from fraudnet.schemas.types import RiskScore, Severity

from brain_behavioural.scorer import (
    HeuristicScorer,
    Scorer,
    ScoringResult,
)

_log = get_logger("brain_behavioural.lgbm")

NUMBER_MODEL_ID = "behavioural-number-lgbm"
WALLET_MODEL_ID = "behavioural-wallet-lgbm"


# Feature ordering is part of the contract between the trainer and the
# serving path. Re-ordering breaks predictions silently — keep these
# tuples authoritative.
NUMBER_FEATURE_ORDER: tuple[str, ...] = (
    "velocity_1m",
    "velocity_5m",
    "velocity_1h",
    "fanout_1h",
    "imei_count",
    "geo_entropy",
    "sms_freq_1h",
)

WALLET_FEATURE_ORDER: tuple[str, ...] = (
    "txn_velocity_1h",
    "counterparty_diversity_24h",
    "value_p95_24h",
)


@dataclass(frozen=True)
class _LoadedModel:
    booster: Any
    version: str


class LightGBMScorer(Scorer):
    """LightGBM scorer wrapping a registry-loaded Booster per entity kind."""

    def __init__(
        self,
        *,
        number_model: _LoadedModel | None,
        wallet_model: _LoadedModel | None,
        fallback: Scorer | None = None,
        signal_threshold: float = 0.7,
    ) -> None:
        self._number = number_model
        self._wallet = wallet_model
        self._fallback = fallback or HeuristicScorer()
        self._threshold = signal_threshold

    @classmethod
    def load_from_registry(
        cls,
        registry,
        *,
        number_model_id: str = NUMBER_MODEL_ID,
        wallet_model_id: str = WALLET_MODEL_ID,
        signal_threshold: float = 0.7,
    ) -> LightGBMScorer:
        number_loaded = _try_load(registry, number_model_id)
        wallet_loaded = _try_load(registry, wallet_model_id)
        return cls(
            number_model=number_loaded,
            wallet_model=wallet_loaded,
            signal_threshold=signal_threshold,
        )

    def score_number(self, features: NumberFeatures) -> ScoringResult:
        if self._number is None:
            return self._fallback.score_number(features)
        x = [_number_feature_value(features, name) for name in NUMBER_FEATURE_ORDER]
        proba = float(self._number.booster.predict([x])[0])
        evidence: dict[str, str | int | float | bool] = {
            name: _number_feature_value(features, name) for name in NUMBER_FEATURE_ORDER
        }
        evidence["proba"] = proba
        return _result(
            value=proba,
            evidence=evidence,
            signal_kind=_number_signal(features) if proba >= self._threshold else None,
            severity=_severity_from_score(proba),
            model_id=NUMBER_MODEL_ID,
            model_version=self._number.version,
        )

    def score_wallet(self, features: WalletFeatures) -> ScoringResult:
        if self._wallet is None:
            return self._fallback.score_wallet(features)
        x = [_wallet_feature_value(features, name) for name in WALLET_FEATURE_ORDER]
        proba = float(self._wallet.booster.predict([x])[0])
        evidence: dict[str, str | int | float | bool] = {
            name: _wallet_feature_value(features, name) for name in WALLET_FEATURE_ORDER
        }
        evidence["proba"] = proba
        return _result(
            value=proba,
            evidence=evidence,
            signal_kind=_wallet_signal(features) if proba >= self._threshold else None,
            severity=_severity_from_score(proba),
            model_id=WALLET_MODEL_ID,
            model_version=self._wallet.version,
        )


def _try_load(registry, model_id: str) -> _LoadedModel | None:
    try:
        from fraudnet.registry import RegistryError
    except ImportError:  # pragma: no cover
        return None
    try:
        manifest = registry.champion(model_id=model_id)
        artifact = registry.fetch_artifact(model_id=model_id, version=manifest.version)
    except RegistryError:
        _log.info("brain_behavioural.no_champion", model_id=model_id)
        return None
    booster = _booster_from_bytes(artifact)
    if booster is None:
        return None
    _log.info(
        "brain_behavioural.model_loaded", model_id=model_id, version=manifest.version
    )
    return _LoadedModel(booster=booster, version=manifest.version)


def _booster_from_bytes(blob: bytes) -> Any | None:
    try:
        import lightgbm as lgb
    except ImportError:
        _log.warning("brain_behavioural.lightgbm_missing")
        return None
    return lgb.Booster(model_str=blob.decode("utf-8"))


def _number_feature_value(f: NumberFeatures, name: str) -> float:
    return float(getattr(f, name))


def _wallet_feature_value(f: WalletFeatures, name: str) -> float:
    return float(getattr(f, name))


def _number_signal(f: NumberFeatures) -> str:
    """Pick a signal_kind that matches the dominant feature when the model
    fires. Keeps decisions-policy YAML routing usable across heuristic and
    ML models — they emit the same signal taxonomy."""
    if f.velocity_1m >= 10 and f.fanout_1h >= 50:
        return "voice.velocity_burst"
    if f.imei_count >= 4:
        return "device.imei_churn"
    if f.sms_freq_1h >= 30:
        return "sms.bulk_template"
    return "voice.velocity_burst"


def _wallet_signal(f: WalletFeatures) -> str:
    if f.value_p95_24h >= 100_000 and f.txn_velocity_1h >= 8:
        return "momo.high_value_velocity"
    return "momo.mule_velocity"


def _severity_from_score(score: float) -> Severity:
    if score >= 0.9:
        return Severity.CRITICAL
    if score >= 0.75:
        return Severity.HIGH
    if score >= 0.5:
        return Severity.MEDIUM
    return Severity.LOW


def _result(
    *,
    value: float,
    evidence: dict[str, str | int | float | bool],
    signal_kind: str | None,
    severity: Severity,
    model_id: str,
    model_version: str,
) -> ScoringResult:
    score = RiskScore(
        value=max(0.0, min(1.0, value)),
        model_id=model_id,
        model_version=model_version,
        computed_at_ms=int(time() * 1000),
        feature_attribution={
            k: float(v) for k, v in evidence.items() if isinstance(v, (int, float))
        },
    )
    return ScoringResult(
        score=score,
        signal_kind=signal_kind,
        severity=severity,
        evidence=evidence,
    )
