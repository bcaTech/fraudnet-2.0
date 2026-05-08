"""Apply infra/kafka-topics/topics.yaml to a Kafka cluster.

Idempotent: missing topics are created; existing topics are left alone (their
partition count is verified — a mismatch is loud but non-fatal so the dev
loop survives a topics.yaml edit).

Production deployment uses a Kafka operator (Strimzi / Confluent for K8s),
not this script. This is the canonical path for local dev only.

Usage:
    python scripts/create_topics.py
    KAFKA_BOOTSTRAP_SERVERS=kafka:29092 python scripts/create_topics.py
    TOPICS_FILE=path/to/topics.yaml python scripts/create_topics.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

# yaml is part of the workspace dev environment via pre-commit; the script
# also runs inside the kafka-init container (Dockerfile.dev) where pyyaml is
# pulled in transitively. Fail loudly on a missing dep so the dev gets a
# helpful message instead of an obscure ImportError.
try:
    import yaml
except ImportError:
    print(
        "create_topics.py needs PyYAML. Install via: uv pip install pyyaml", file=sys.stderr
    )
    sys.exit(2)

try:
    from confluent_kafka.admin import AdminClient, ConfigResource, NewTopic  # type: ignore[import-untyped]
except ImportError:
    print(
        "create_topics.py needs confluent-kafka. "
        "Install via: uv pip install 'confluent-kafka>=2.6'",
        file=sys.stderr,
    )
    sys.exit(2)


BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPICS_FILE = os.environ.get(
    "TOPICS_FILE", str(Path(__file__).resolve().parent.parent / "infra" / "kafka-topics" / "topics.yaml")
)
WAIT_SECS = int(os.environ.get("KAFKA_WAIT_SECS", "60"))


def wait_for_kafka(admin: AdminClient, deadline: float) -> None:
    """Block until the broker answers list_topics, or raise."""
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            admin.list_topics(timeout=2.0)
            return
        except Exception as exc:  # noqa: BLE001 — kafka client raises a wide tree
            last_err = exc
            time.sleep(1.0)
    raise RuntimeError(f"Kafka at {BOOTSTRAP} not reachable within {WAIT_SECS}s: {last_err}")


def load_topics(path: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("defaults", {}) or {}, data.get("topics", []) or []


def merge_config(default: dict[str, Any], override: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (default or {}).items():
        out[str(k)] = str(v)
    for k, v in (override or {}).items():
        out[str(k)] = str(v)
    return out


def main() -> int:
    print(f"create_topics → bootstrap={BOOTSTRAP} topics_file={TOPICS_FILE}")

    if not Path(TOPICS_FILE).exists():
        print(f"  topics file not found: {TOPICS_FILE}", file=sys.stderr)
        return 2

    defaults, topics = load_topics(TOPICS_FILE)
    default_rf = int(defaults.get("replication_factor", 1))
    default_config = defaults.get("config", {}) or {}

    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    deadline = time.monotonic() + WAIT_SECS
    wait_for_kafka(admin, deadline)

    existing = set(admin.list_topics(timeout=10.0).topics.keys())

    to_create: list[NewTopic] = []
    skipped: list[str] = []
    for spec in topics:
        name = spec["name"]
        if name in existing:
            skipped.append(name)
            continue
        partitions = int(spec["partitions"])
        rf = int(spec.get("replication_factor", default_rf))
        config = merge_config(default_config, spec.get("config"))
        to_create.append(NewTopic(name, num_partitions=partitions, replication_factor=rf, config=config))

    created: list[str] = []
    failed: list[str] = []
    if to_create:
        futures = admin.create_topics(to_create, request_timeout=15.0)
        for name, fut in futures.items():
            try:
                fut.result(timeout=15.0)
                created.append(name)
            except Exception as exc:  # noqa: BLE001
                # 36 = TopicAlreadyExists. Treat as success — race with another runner.
                if "TopicExistsError" in type(exc).__name__ or "already exists" in str(exc).lower():
                    skipped.append(name)
                else:
                    print(f"  ✗ {name}: {exc}", file=sys.stderr)
                    failed.append(name)

    print(f"  created: {len(created)}")
    for name in created:
        print(f"    + {name}")
    print(f"  existed: {len(skipped)}")
    for name in skipped:
        print(f"    = {name}")

    if failed:
        print(f"  failed:  {len(failed)}", file=sys.stderr)
        return 1

    # Sanity-check partition counts on existing topics — a soft check that
    # surfaces drift between topics.yaml and the cluster.
    md = admin.list_topics(timeout=10.0)
    drift = []
    for spec in topics:
        name = spec["name"]
        want = int(spec["partitions"])
        actual = len(md.topics[name].partitions) if name in md.topics else None
        if actual is not None and actual != want:
            drift.append((name, want, actual))
    if drift:
        print("  partition drift detected (will not auto-resize):")
        for name, want, actual in drift:
            print(f"    ! {name}: want={want} actual={actual}")

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
