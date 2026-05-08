from __future__ import annotations

from brain_content.classifier import HeuristicContentClassifier
from brain_content.ml_classifier import TfidfLrClassifier, _LoadedPipeline
from brain_content.url_reputation import StaticBlocklist


class _FakePipeline:
    def __init__(self, proba_pos: float) -> None:
        self._p = proba_pos

    def predict_proba(self, docs):  # noqa: ANN001
        return [[1.0 - self._p, self._p] for _ in docs]


def _heuristic() -> HeuristicContentClassifier:
    return HeuristicContentClassifier(url_reputation=StaticBlocklist(bad_domains=[]))


def test_falls_back_to_heuristic_when_no_pipeline_loaded() -> None:
    clf = TfidfLrClassifier(heuristic=_heuristic(), loaded=None)
    r = clf.classify(body="hello world", body_hash=None, template_hash=None)
    assert r.signal_kind is None  # below heuristic thresholds


def test_ml_signals_when_above_threshold() -> None:
    clf = TfidfLrClassifier(
        heuristic=_heuristic(),
        loaded=_LoadedPipeline(pipeline=_FakePipeline(0.85), version="t-1"),
        signal_threshold=0.5,
    )
    r = clf.classify(body="claim your prize urgent", body_hash=None, template_hash=None)
    assert r.signal_kind == "sms.template_smishing"
    assert r.score.value >= 0.5
    assert r.evidence["model_proba"] == 0.85


def test_keeps_strong_heuristic_match_over_ml() -> None:
    """body_hash-known-bad fires before the ML path. Don't double-classify."""
    heuristic = HeuristicContentClassifier(
        url_reputation=StaticBlocklist(bad_domains=[]),
        bad_body_hashes=["abc"],
    )
    clf = TfidfLrClassifier(
        heuristic=heuristic,
        loaded=_LoadedPipeline(pipeline=_FakePipeline(0.99), version="t-1"),
    )
    r = clf.classify(body="anything", body_hash="abc", template_hash=None)
    assert r.signal_kind == "sms.known_bad_body"
