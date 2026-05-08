"""Federation router — auth gate, lookup, subgraph, publish.

Uses FastAPI's TestClient + InMemoryFederationAdapter.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fraudnet.federation import (
    InMemoryFederationAdapter,
    create_router,
    hash_identifier,
)
from fraudnet.federation.auth import sign_request
from fraudnet.federation.server import FederationServerSettings


def _build_app(adapter: InMemoryFederationAdapter) -> tuple[FastAPI, str]:
    settings = FederationServerSettings(
        server_id="opco-ghana",
        salt_version="v1",
        peer_secrets={"opco-uganda": "shared-secret-uganda"},
    )
    app = FastAPI()
    app.include_router(create_router(settings=settings, adapter=adapter))
    return app, "shared-secret-uganda"


def _signed_post(client: TestClient, secret: str, path: str, body: bytes) -> object:
    headers = sign_request(secret=secret, method="POST", path=path, body=body)
    headers["X-Federation-Peer"] = "opco-uganda"
    headers["Content-Type"] = "application/json"
    return client.post(path, content=body, headers=headers)


def test_lookup_unsigned_request_fails_401() -> None:
    adapter = InMemoryFederationAdapter()
    app, _ = _build_app(adapter)
    client = TestClient(app)
    r = client.post(
        "/federation/v1/flags/lookup", json={"identifier_hashes": ["a" * 64]}
    )
    assert r.status_code == 401


def test_lookup_returns_matched_only() -> None:
    """Unknown hashes are silently dropped — does not leak the absence of
    a subscriber."""
    adapter = InMemoryFederationAdapter()
    h = adapter.add_flag(
        identifier="+233200000001",
        identifier_kind="msisdn",
        indicator_kind="mule",
        confidence=0.9,
    )
    app, secret = _build_app(adapter)
    client = TestClient(app)
    body = (
        b'{"identifier_hashes":["'
        + h.encode()
        + b'","'
        + (b"f" * 64)
        + b'"]}'
    )
    r = _signed_post(client, secret, "/federation/v1/flags/lookup", body)
    assert r.status_code == 200, r.text
    payload = r.json()
    matched = payload["matched"]
    assert len(matched) == 1
    assert matched[0]["identifier_hash"] == h


def test_subgraph_returns_hashed_neighbourhood() -> None:
    adapter = InMemoryFederationAdapter()
    h_seed = adapter.add_node(identifier="+233200000001", kind="Number", risk_score=0.9)
    h_other = adapter.add_node(identifier="+233200000002", kind="Number", risk_score=0.7)
    adapter.add_edge(src_hash=h_seed, dst_hash=h_other, kind="CALLED", ts_ms=100)

    app, secret = _build_app(adapter)
    client = TestClient(app)
    body = (
        b'{"seed_hashes":["'
        + h_seed.encode()
        + b'"], "depth": 1, "max_nodes": 50}'
    )
    r = _signed_post(client, secret, "/federation/v1/subgraph/query", body)
    assert r.status_code == 200, r.text
    payload = r.json()
    ids = {n["identifier_hash"] for n in payload["nodes"]}
    assert h_seed in ids
    assert h_other in ids
    assert payload["edges"][0]["kind"] == "CALLED"
    # Cardinal rule: no plaintext on the wire.
    raw = r.text
    assert "+233" not in raw


def test_publish_accept() -> None:
    adapter = InMemoryFederationAdapter()
    app, secret = _build_app(adapter)
    client = TestClient(app)
    h = hash_identifier("+233200000099", kind="msisdn")
    body = (
        b'{"flag":{"identifier_hash":"'
        + h.encode()
        + b'","identifier_kind":"msisdn","indicator_kind":"mule",'
        b'"confidence":0.9,"first_seen_ms":0,"last_seen_ms":0,"evidence":{}}}'
    )
    r = _signed_post(client, secret, "/federation/v1/flags/publish", body)
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True
    assert len(adapter.inbound_flags) == 1
    assert adapter.inbound_flags[0].accepted_from == "opco-uganda"


def test_unknown_peer_rejected() -> None:
    adapter = InMemoryFederationAdapter()
    app, secret = _build_app(adapter)
    client = TestClient(app)
    body = b'{"identifier_hashes":["' + (b"a" * 64) + b'"]}'
    headers = sign_request(
        secret=secret, method="POST", path="/federation/v1/flags/lookup", body=body
    )
    headers["X-Federation-Peer"] = "opco-unknown"
    headers["Content-Type"] = "application/json"
    r = client.post("/federation/v1/flags/lookup", content=body, headers=headers)
    assert r.status_code == 401
