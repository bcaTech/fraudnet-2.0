from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fraudnet.features.client import InMemoryFeatureStore
from fraudnet.features.snapshot import NumberFeatures, WalletFeatures
from brain_behavioural.main import create_app
from brain_behavioural.scorer import HeuristicScorer


@pytest.fixture
def client() -> tuple[TestClient, InMemoryFeatureStore]:
    store = InMemoryFeatureStore()
    app = create_app(scorer=HeuristicScorer(), feature_store=store)
    return TestClient(app), store


class TestScoreNumber:
    def test_returns_404_when_no_features(
        self, client: tuple[TestClient, InMemoryFeatureStore]
    ) -> None:
        c, _ = client
        r = c.post("/score/number", json={"msisdn": "+233241234567"})
        assert r.status_code == 404

    def test_returns_score_when_features_exist(
        self, client: tuple[TestClient, InMemoryFeatureStore]
    ) -> None:
        c, store = client
        import asyncio

        asyncio.get_event_loop().run_until_complete(
            store.put_number(NumberFeatures(msisdn="+233241234567", velocity_1m=12, fanout_1h=80))
        )
        r = c.post("/score/number", json={"msisdn": "+233241234567"})
        assert r.status_code == 200
        body = r.json()
        assert body["signal_kind"] == "voice.velocity_burst"
        assert body["severity"] == "high"
        assert body["score"] > 0.9
        assert body["model_id"] == "behavioural-heuristic"


def test_health_live(client: tuple[TestClient, InMemoryFeatureStore]) -> None:
    c, _ = client
    assert c.get("/health/live").status_code == 200
