from __future__ import annotations

from fraudnet.features.snapshot import NumberFeatures, WalletFeatures

from brain_behavioural.lgbm_scorer import LightGBMScorer
from brain_behavioural.scorer import HeuristicScorer


def test_falls_back_to_heuristic_when_no_model_loaded() -> None:
    scorer = LightGBMScorer(
        number_model=None, wallet_model=None, fallback=HeuristicScorer()
    )
    f = NumberFeatures(msisdn="+233241234567", velocity_1m=12, fanout_1h=80)
    r = scorer.score_number(f)
    assert r.signal_kind == "voice.velocity_burst"
    # fallback scorer keeps its own model_id
    assert r.score.model_id == "behavioural-heuristic"


def test_wallet_falls_back_when_no_model_loaded() -> None:
    scorer = LightGBMScorer(number_model=None, wallet_model=None)
    f = WalletFeatures(
        wallet_id="W1", txn_velocity_1h=20, counterparty_diversity_24h=10
    )
    r = scorer.score_wallet(f)
    assert r.signal_kind == "momo.mule_velocity"


class _FakeBooster:
    def __init__(self, value: float) -> None:
        self._value = value

    def predict(self, X):  # noqa: ANN001, N802
        return [self._value for _ in X]


def test_uses_lgbm_when_loaded_and_above_threshold() -> None:
    from brain_behavioural.lgbm_scorer import _LoadedModel

    scorer = LightGBMScorer(
        number_model=_LoadedModel(booster=_FakeBooster(0.95), version="2026.05.08-1"),
        wallet_model=None,
        fallback=HeuristicScorer(),
        signal_threshold=0.5,
    )
    f = NumberFeatures(msisdn="+233241234567", velocity_1m=12, fanout_1h=80)
    r = scorer.score_number(f)
    assert r.signal_kind == "voice.velocity_burst"
    assert r.score.model_id == "behavioural-number-lgbm"
    assert r.score.value == 0.95


def test_lgbm_below_threshold_emits_no_signal() -> None:
    from brain_behavioural.lgbm_scorer import _LoadedModel

    scorer = LightGBMScorer(
        number_model=_LoadedModel(booster=_FakeBooster(0.2), version="v1"),
        wallet_model=None,
        signal_threshold=0.7,
    )
    f = NumberFeatures(msisdn="+233241234567")
    r = scorer.score_number(f)
    assert r.signal_kind is None
    assert r.score.value == 0.2
