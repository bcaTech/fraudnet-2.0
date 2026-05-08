from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from url_intel.blocklist import in_memory_blocklist
from url_intel.main import create_app


@pytest.fixture
def client() -> TestClient:
    bl = in_memory_blocklist(allow_list=["google.com", "mtn.com.gh"])
    app = create_app(blocklist=bl)
    return TestClient(app)


class TestCheck:
    def test_check_unknown_returns_blocked_false(self, client: TestClient) -> None:
        r = client.get("/blocklist/check", params={"url": "safe.example"})
        assert r.status_code == 200
        assert r.json()["blocked"] is False

    def test_check_blocked(self, client: TestClient) -> None:
        client.post("/blocklist/add", json={"domain": "bad.example", "category": "phishing"})
        r = client.get("/blocklist/check", params={"url": "https://login.bad.example/x"})
        body = r.json()
        assert body["blocked"] is True
        assert body["matched"] == "bad.example"
        assert body["category"] == "phishing"

    def test_check_allow_listed(self, client: TestClient) -> None:
        r = client.get("/blocklist/check", params={"url": "google.com"})
        assert r.json()["allow_listed"] is True
        assert r.json()["blocked"] is False


class TestAddRemove:
    def test_add_then_remove(self, client: TestClient) -> None:
        r = client.post("/blocklist/add", json={"domain": "evil.example"})
        assert r.json()["added"] is True
        r2 = client.post("/blocklist/remove", json={"domain": "evil.example"})
        assert r2.json()["removed"] is True

    def test_add_allow_listed_returns_added_false(self, client: TestClient) -> None:
        r = client.post("/blocklist/add", json={"domain": "google.com"})
        body = r.json()
        assert body["added"] is False
        assert body["reason"] == "allow_listed"


class TestFeedImport:
    def test_imports_filter_allow_list(self, client: TestClient) -> None:
        r = client.post(
            "/feeds/import",
            json={
                "feed": "phishtank",
                "entries": [
                    {"domain": "phish1.example", "category": "phishing", "confidence": 0.95},
                    {"domain": "phish2.example", "category": "phishing", "confidence": 0.95},
                    {"domain": "google.com", "category": "phishing", "confidence": 0.95},
                    {"domain": "not_a_domain", "category": "phishing", "confidence": 0.95},
                ],
            },
        )
        body = r.json()
        assert body["submitted"] == 4
        assert body["added"] == 2
        assert body["rejected_allow_listed"] == 1
        assert body["rejected_invalid"] == 1

    def test_empty_entries_400(self, client: TestClient) -> None:
        r = client.post("/feeds/import", json={"feed": "x", "entries": []})
        assert r.status_code == 400


class TestExport:
    def test_export_lists_added_domains(self, client: TestClient) -> None:
        client.post("/blocklist/add", json={"domain": "a.example"})
        client.post("/blocklist/add", json={"domain": "b.example"})
        r = client.get("/blocklist/export")
        body = r.json()
        assert body["count"] == 2
        assert "a.example" in body["domains"]
        assert "b.example" in body["domains"]


class TestHealth:
    def test_live(self, client: TestClient) -> None:
        assert client.get("/health/live").json() == {"status": "ok"}

    def test_ready(self, client: TestClient) -> None:
        assert client.get("/health/ready").json() == {"status": "ready"}
