from __future__ import annotations

from fastapi.testclient import TestClient

from brain_content.classifier import HeuristicContentClassifier
from brain_content.main import create_app
from brain_content.url_reputation import StaticBlocklist


def _client() -> TestClient:
    classifier = HeuristicContentClassifier(
        url_reputation=StaticBlocklist(bad_domains={"scam.example"}),
    )
    return TestClient(create_app(classifier=classifier))


def test_score_sms_url_match() -> None:
    c = _client()
    r = c.post(
        "/score/sms",
        json={"body": "Click https://scam.example/win to claim", "body_hash": None, "template_hash": None},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["signal_kind"] == "sms.malicious_url"
    assert "https://scam.example/win" in body["matched_urls"]


def test_score_sms_no_body_no_match() -> None:
    c = _client()
    r = c.post(
        "/score/sms",
        json={"body": None, "body_hash": "sha256:unknown", "template_hash": None},
    )
    assert r.status_code == 200
    assert r.json()["signal_kind"] is None


def test_health_live() -> None:
    c = _client()
    assert c.get("/health/live").status_code == 200
