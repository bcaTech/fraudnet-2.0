"""Helpers for loading the Avro schemas shipped with the package.

Used by the kafka-client to register schemas with Confluent Schema Registry
on producer init, and by tools and tests to validate payloads.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

_AVRO_PACKAGE = "fraudnet.schemas.avro"


@lru_cache(maxsize=1)
def _avro_root() -> Path:
    # When the package is built, avro/ is force-included into the wheel under
    # fraudnet/schemas/avro. When running from source, fall back to the repo
    # path so tests don't depend on a built wheel.
    try:
        return Path(str(files(_AVRO_PACKAGE)))
    except (ModuleNotFoundError, FileNotFoundError):
        return Path(__file__).resolve().parents[3] / "avro"


def avro_schema(topic: str) -> dict[str, object]:
    """Load the Avro schema for a topic by name (e.g. 'momo.events.v1')."""
    path = _avro_root() / f"{topic}.avsc"
    if not path.exists():
        raise FileNotFoundError(f"avro schema not found for topic {topic}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def all_topics() -> list[str]:
    """Topic names for which an Avro schema is shipped."""
    return sorted(p.stem for p in _avro_root().glob("*.avsc"))
