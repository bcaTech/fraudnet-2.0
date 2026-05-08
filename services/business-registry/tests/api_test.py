from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from business_registry.main import create_app
from business_registry.registry import InMemoryRegistry


@pytest.fixture
def client() -> TestClient:
    registry = InMemoryRegistry()
    return TestClient(create_app(registry=registry))


def test_create_and_get(client: TestClient) -> None:
    r = client.post("/businesses", json={"name": "Acme", "registration_number": "GH-1"})
    assert r.status_code == 201
    biz_id = r.json()["id"]
    r2 = client.get(f"/businesses/{biz_id}")
    assert r2.status_code == 200
    assert r2.json()["name"] == "Acme"


def test_verify_msisdn_flow(client: TestClient) -> None:
    biz = client.post("/businesses", json={"name": "Ecobank"}).json()
    bid = biz["id"]
    client.post(f"/businesses/{bid}/msisdns", json={"msisdn": "+233231100000"})
    # Before verification — matched but not verified.
    pre = client.get("/lookup/msisdn/+233231100000").json()
    assert pre["matched"] is True
    assert pre["is_verified"] is False
    client.post(f"/businesses/{bid}/verify")
    post = client.get("/lookup/msisdn/+233231100000").json()
    assert post["is_verified"] is True


def test_shortcode_lookup_unknown(client: TestClient) -> None:
    r = client.get("/lookup/shortcode/UNKNOWN")
    body = r.json()
    assert body["matched"] is False
    assert body["business"] is None


def test_add_msisdn_to_unknown_business_404(client: TestClient) -> None:
    r = client.post(
        "/businesses/00000000-0000-0000-0000-000000000099/msisdns",
        json={"msisdn": "+233244000000"},
    )
    assert r.status_code == 404


def test_list_filter(client: TestClient) -> None:
    a = client.post("/businesses", json={"name": "A Co"}).json()
    client.post("/businesses", json={"name": "B Co"})
    client.post(f"/businesses/{a['id']}/verify")
    verified = client.get("/businesses?status=verified").json()
    assert len(verified) == 1
    assert verified[0]["name"] == "A Co"
