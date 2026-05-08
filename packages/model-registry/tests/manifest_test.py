from __future__ import annotations

from fraudnet.registry import ModelManifest


def test_manifest_roundtrip() -> None:
    m = ModelManifest(
        model_id="behavioural-number-lgbm",
        version="2026.05.08-120000",
        created_at_ms=1_700_000_000_000,
        artifact_sha256="abc" * 21,
        artifact_size_bytes=4096,
        artifact_format="lightgbm",
        metrics={"auc_train": 0.92},
        notes="trained on demo",
    )
    raw = m.to_dict()
    rebuilt = ModelManifest.from_dict(raw)
    assert rebuilt == m
