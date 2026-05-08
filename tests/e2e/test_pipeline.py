"""End-to-end pipeline test.

Exercises the full Phase 1 streaming path against a live docker-compose stack:

  produce  → momo.events.v1
            ↓
  ingest   (already in-topic)
            ↓
  stream-graph    → Memgraph: Wallet node + edges visible
  stream-features → Aerospike: feature snapshot for the wallet visible
  brain-behavioural → fraud.signals.v1: a SignalEventV1 keyed on the wallet
            ↓
  decisions       → action.tier1.v1 / action.tier2.v1: dispatch present

The test is opt-in (`FRAUDNET_E2E=1`) and skipped by default — it requires
the full stack from `make services-up`. Each assertion has a generous
timeout because the brain layer's heuristic threshold and the buffered
graph writer add seconds of natural latency.

Tagged @pytest.mark.e2e so `make test-e2e` picks it up exclusively.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest

# Optional imports — surfaced as a skip if the dev deps are missing rather
# than as an ImportError, since this file lives outside any service tree.
pytest.importorskip("confluent_kafka", reason="confluent-kafka is required for e2e tests")
pytest.importorskip("neo4j", reason="neo4j (Memgraph driver) is required for e2e tests")

from confluent_kafka import Consumer, Producer  # noqa: E402
from confluent_kafka.admin import AdminClient  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _wait_until(predicate, *, timeout_s: float, label: str, interval_s: float = 0.5) -> Any:  # noqa: ANN001
    """Block until `predicate()` returns truthy or timeout. Returns the value."""
    deadline = time.monotonic() + timeout_s
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval_s)
    raise AssertionError(f"timeout after {timeout_s:.0f}s waiting for: {label} (last={last!r})")


def _make_producer(bootstrap: str) -> Producer:
    return Producer(
        {
            "bootstrap.servers": bootstrap,
            "client.id": "fraudnet-e2e",
            "linger.ms": 5,
            "compression.type": "zstd",
            "enable.idempotence": True,
            "acks": "all",
        }
    )


def _make_consumer(bootstrap: str, group_id: str, topics: list[str]) -> Consumer:
    c = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        }
    )
    c.subscribe(topics)
    return c


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def unique_ids() -> dict[str, str]:
    """Unique identifiers so the test does not collide with seeded data."""
    suffix = uuid.uuid4().hex[:10]
    msisdn = f"+233244{suffix[:6]}"
    wallet_id = f"W:233244{suffix[:6]}"
    return {
        "msisdn": msisdn,
        "wallet_id": wallet_id,
        "txn_prefix": f"e2e_{suffix}",
        "tenant_id": "mtn-ghana",
    }


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def test_topology_topics_exist(kafka_bootstrap: str) -> None:
    """Sanity: kafka-init has run and the topics this test needs are present."""
    admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
    md = admin.list_topics(timeout=10.0)
    required = {
        "momo.events.v1",
        "graph.mutations.v1",
        "fraud.signals.v1",
        "action.tier1.v1",
        "action.tier2.v1",
        "decisions.dispatched.v1",
    }
    missing = required - set(md.topics.keys())
    assert not missing, f"required topics missing: {missing}"


# ---------------------------------------------------------------------------
# Pipeline test — produce a MoMo event, follow it through every layer.
# ---------------------------------------------------------------------------


def _produce_momo_burst(producer: Producer, topic: str, ids: dict[str, str], count: int) -> None:
    """Burst of MoMo SENT events from one wallet — designed to trip the
    behavioural heuristic for high MoMo velocity (DECISIONS.md D-006 notes
    the model is a velocity / fan-out heuristic in Phase 1)."""
    for i in range(count):
        payload = {
            "event_id": f"{ids['txn_prefix']}_{i:04d}",
            "event_ts_ms": _now_ms(),
            "ingest_ts_ms": _now_ms(),
            "source": "e2e-pipeline-test",
            "tenant_id": ids["tenant_id"],
            "kind": "send",
            "txn_id": f"{ids['txn_prefix']}_{i:04d}",
            "sender_wallet_id": ids["wallet_id"],
            "recipient_wallet_id": f"W:{uuid.uuid4().hex[:12]}",
            "sender_msisdn": ids["msisdn"],
            "recipient_msisdn": f"+233244{uuid.uuid4().hex[:6]}",
            "amount_minor": 50_00 + i * 100,
            "currency": "GHS",
            "counterparty_kind": "wallet",
            "counterparty_account_hash": None,
            "is_reversal_of": None,
            "channel": "ussd",
        }
        producer.produce(
            topic,
            key=ids["wallet_id"].encode(),
            value=json.dumps(payload).encode(),
            headers=[("e2e-test", b"true"), ("content-type", b"application/json")],
        )
    producer.flush(timeout=10.0)


def test_pipeline_momo_to_dispatch(
    kafka_bootstrap: str,
    memgraph_url: str,
    aerospike_hosts: list[tuple[str, int]],
    unique_ids: dict[str, str],
) -> None:
    """A burst of MoMo sends from a fresh wallet should:
       1. land a Wallet node in Memgraph (stream-graph),
       2. produce a feature snapshot in Aerospike (stream-features),
       3. emit a fraud signal on fraud.signals.v1 (brain-behavioural),
       4. trigger a tier-2 dispatch (decisions → action.tier2.v1).
    """
    producer = _make_producer(kafka_bootstrap)

    # Subscribe to downstream topics BEFORE producing so we don't miss the
    # eventual events under "auto.offset.reset=latest".
    signals_consumer = _make_consumer(
        kafka_bootstrap, f"e2e-signals-{uuid.uuid4().hex[:6]}", ["fraud.signals.v1"]
    )
    tier_consumer = _make_consumer(
        kafka_bootstrap,
        f"e2e-tiers-{uuid.uuid4().hex[:6]}",
        ["action.tier1.v1", "action.tier2.v1", "decisions.dispatched.v1"],
    )
    # Prime the consumers (poll once so partition assignment happens).
    signals_consumer.poll(2.0)
    tier_consumer.poll(2.0)

    try:
        _produce_momo_burst(producer, "momo.events.v1", unique_ids, count=80)

        # ---- 1) stream-graph: Memgraph node visible -----------------------
        def memgraph_has_wallet() -> bool:
            with GraphDatabase.driver(memgraph_url) as driver, driver.session() as s:
                rec = s.run(
                    "MATCH (w:Wallet {wallet_id: $wid}) RETURN count(w) AS n",
                    wid=unique_ids["wallet_id"],
                ).single()
                return rec is not None and rec["n"] > 0

        _wait_until(memgraph_has_wallet, timeout_s=30.0,
                    label=f"Memgraph Wallet({unique_ids['wallet_id']})")

        # ---- 2) stream-features: Aerospike snapshot for the wallet --------
        try:
            import aerospike  # type: ignore[import-not-found]
        except ImportError:
            pytest.skip("aerospike client not installed — skipping feature-store assertion")

        client = aerospike.client({"hosts": aerospike_hosts}).connect()
        try:
            def aerospike_has_features() -> bool:
                try:
                    _, _, bins = client.get(("fraudnet", "wallets", unique_ids["wallet_id"]))
                except aerospike.exception.RecordNotFound:
                    return False
                # Any of these implies stream-features wrote something.
                return any(k in bins for k in ("momo_vel_1h", "momo_vel_24h", "value_p95_24h"))

            _wait_until(aerospike_has_features, timeout_s=30.0,
                        label=f"Aerospike wallets:{unique_ids['wallet_id']}")
        finally:
            client.close()

        # ---- 3) brain-behavioural: signal on fraud.signals.v1 -------------
        def find_signal() -> dict[str, Any] | None:
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                msg = signals_consumer.poll(0.2)
                if msg is None or msg.error():
                    continue
                value = msg.value()
                if not value:
                    continue
                # Avro framing — first byte is a magic 0x00; we can't decode
                # without a schema-registry deserialiser. Accept either Avro
                # or JSON: the test only needs to verify a signal occurred
                # for our wallet, by inspecting message KEY (subject).
                key = msg.key()
                if key is None:
                    continue
                key_str = key.decode("utf-8", errors="ignore")
                if unique_ids["wallet_id"] in key_str or unique_ids["msisdn"] in key_str:
                    return {"key": key_str, "topic": msg.topic()}
                # Fallback: try JSON decode for backends that produce JSON.
                try:
                    body = json.loads(value)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                subj = (body.get("subject") or {}).get("id", "")
                if subj == unique_ids["wallet_id"] or subj == unique_ids["msisdn"]:
                    return body
            return None

        _wait_until(find_signal, timeout_s=45.0, interval_s=0.1,
                    label="fraud.signals.v1 signal for our wallet")

        # ---- 4) decisions: tier dispatch ---------------------------------
        def find_tier_dispatch() -> dict[str, Any] | None:
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                msg = tier_consumer.poll(0.2)
                if msg is None or msg.error():
                    continue
                key = msg.key()
                if key is None:
                    continue
                key_str = key.decode("utf-8", errors="ignore")
                if unique_ids["wallet_id"] in key_str or unique_ids["msisdn"] in key_str:
                    return {"topic": msg.topic(), "key": key_str}
            return None

        dispatch = _wait_until(find_tier_dispatch, timeout_s=45.0, interval_s=0.1,
                               label="action.tier{1,2}.v1 dispatch for our wallet")
        assert dispatch["topic"] in (
            "action.tier1.v1", "action.tier2.v1", "decisions.dispatched.v1"
        )
    finally:
        signals_consumer.close()
        tier_consumer.close()
