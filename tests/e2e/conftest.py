"""Shared fixtures for the end-to-end pipeline tests.

The e2e suite runs against a live docker-compose stack (`make services-up`).
By default it is skipped — opt in with `FRAUDNET_E2E=1` or by passing
`-m e2e` to pytest. The skip lives in the pytest_collection_modifyitems
hook so a misconfigured environment fails fast and loud.
"""

from __future__ import annotations

import os
import socket

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e: end-to-end pipeline tests against a live docker-compose stack",
    )


def _reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get("FRAUDNET_E2E") == "1":
        return  # opted in — let everything run

    skip_e2e = pytest.mark.skip(
        reason="e2e tests require FRAUDNET_E2E=1 and a live compose stack"
    )
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


@pytest.fixture(scope="session")
def kafka_bootstrap() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


@pytest.fixture(scope="session")
def schema_registry_url() -> str:
    return os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081")


@pytest.fixture(scope="session")
def memgraph_url() -> str:
    return os.environ.get("MEMGRAPH_URL", "bolt://localhost:7687")


@pytest.fixture(scope="session")
def aerospike_hosts() -> list[tuple[str, int]]:
    raw = os.environ.get("AEROSPIKE_HOSTS", "localhost:3010")
    out = []
    for chunk in raw.split(","):
        host, _, port = chunk.strip().partition(":")
        out.append((host, int(port or "3000")))
    return out


@pytest.fixture(scope="session", autouse=True)
def _stack_alive(kafka_bootstrap: str) -> None:
    """Fail fast with a clear message if the user opted into e2e but the
    stack isn't running."""
    if os.environ.get("FRAUDNET_E2E") != "1":
        return
    host, _, port = kafka_bootstrap.partition(":")
    if not _reachable(host, int(port or "9092")):
        pytest.exit(
            f"e2e: Kafka unreachable at {kafka_bootstrap}. "
            f"Run `make infra-up && make services-up` first.",
            returncode=2,
        )
