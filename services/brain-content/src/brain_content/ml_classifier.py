"""TF-IDF + Logistic Regression content classifier.

Phase 2 ML upgrade. The artifact stored in the registry is a pickled
sklearn `Pipeline([TfidfVectorizer, LogisticRegression])`. The pipeline
is what the trainer fits and what we predict on; we pickle the whole
pipeline so the vectoriser config travels with the weights.

Falls back to the heuristic classifier if the registry has no champion
or sklearn isn't installed. The heuristic also runs as a co-classifier:
its hash-lookup paths catch known-bad content faster than the model and
land tighter signal_kinds (sms.known_bad_template etc.). The model
adds coverage for novel templates the heuristic doesn't recognise.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from time import time
from typing import Any

from fraudnet.obs import get_logger
from fraudnet.schemas.types import RiskScore, Severity

from brain_content.classifier import (
    ClassificationResult,
    ContentClassifier,
    HeuristicContentClassifier,
)

_log = get_logger("brain_content.ml")

CONTENT_MODEL_ID = "content-tfidf-lr"
ML_MODEL_VERSION_FALLBACK = "0.0.0"


@dataclass(frozen=True)
class _LoadedPipeline:
    pipeline: Any
    version: str


class TfidfLrClassifier(ContentClassifier):
    """Wraps the heuristic classifier with a TF-IDF + LR fallback for novel
    content. Hash and URL-reputation hits return the heuristic's high-
    confidence signal. Otherwise the model speaks."""

    def __init__(
        self,
        *,
        heuristic: HeuristicContentClassifier,
        loaded: _LoadedPipeline | None,
        signal_threshold: float = 0.6,
    ) -> None:
        self._heuristic = heuristic
        self._loaded = loaded
        self._threshold = signal_threshold

    @classmethod
    def load_from_registry(
        cls,
        registry,
        *,
        heuristic: HeuristicContentClassifier,
        model_id: str = CONTENT_MODEL_ID,
        signal_threshold: float = 0.6,
    ) -> TfidfLrClassifier:
        loaded = _try_load_pipeline(registry, model_id)
        return cls(
            heuristic=heuristic,
            loaded=loaded,
            signal_threshold=signal_threshold,
        )

    def classify(
        self,
        *,
        body: str | None,
        body_hash: str | None,
        template_hash: str | None,
    ) -> ClassificationResult:
        baseline = self._heuristic.classify(
            body=body, body_hash=body_hash, template_hash=template_hash
        )
        # Heuristic fired with high confidence — keep its signal verbatim.
        if baseline.signal_kind is not None and baseline.score.value >= 0.7:
            return baseline
        if self._loaded is None or not body:
            return baseline

        try:
            proba = float(self._loaded.pipeline.predict_proba([body])[0][1])
        except Exception as exc:  # noqa: BLE001
            _log.warning("brain_content.ml_predict_failed", error=str(exc))
            return baseline

        evidence = dict(baseline.evidence)
        evidence["model_proba"] = proba
        evidence["model_version"] = self._loaded.version
        if proba >= self._threshold:
            return ClassificationResult(
                score=_score(proba, evidence, self._loaded.version),
                signal_kind="sms.template_smishing",
                severity=_severity(proba),
                evidence=evidence,
                matched_urls=baseline.matched_urls,
            )
        # Below threshold: keep baseline shape but report the proba.
        return ClassificationResult(
            score=_score(max(baseline.score.value, proba), evidence, self._loaded.version),
            signal_kind=baseline.signal_kind,
            severity=baseline.severity,
            evidence=evidence,
            matched_urls=baseline.matched_urls,
        )


def _try_load_pipeline(registry, model_id: str) -> _LoadedPipeline | None:
    try:
        from fraudnet.registry import RegistryError
    except ImportError:  # pragma: no cover
        return None
    try:
        manifest = registry.champion(model_id=model_id)
        artifact = registry.fetch_artifact(model_id=model_id, version=manifest.version)
    except RegistryError:
        _log.info("brain_content.no_champion", model_id=model_id)
        return None
    pipeline = _pipeline_from_bytes(artifact)
    if pipeline is None:
        return None
    _log.info("brain_content.model_loaded", model_id=model_id, version=manifest.version)
    return _LoadedPipeline(pipeline=pipeline, version=manifest.version)


def _pipeline_from_bytes(blob: bytes) -> Any | None:
    try:
        import sklearn  # noqa: F401
    except ImportError:
        _log.warning("brain_content.sklearn_missing")
        return None
    try:
        return pickle.loads(blob)  # noqa: S301 — registry-trusted bytes
    except Exception as exc:  # noqa: BLE001
        _log.error("brain_content.unpickle_failed", error=str(exc))
        return None


def _score(value: float, evidence: dict[str, str | int | float | bool], version: str) -> RiskScore:
    return RiskScore(
        value=max(0.0, min(1.0, value)),
        model_id=CONTENT_MODEL_ID,
        model_version=version,
        computed_at_ms=int(time() * 1000),
        feature_attribution={
            k: float(v) for k, v in evidence.items() if isinstance(v, (int, float))
        },
    )


def _severity(score: float) -> Severity:
    if score >= 0.9:
        return Severity.CRITICAL
    if score >= 0.7:
        return Severity.HIGH
    if score >= 0.5:
        return Severity.MEDIUM
    return Severity.LOW
