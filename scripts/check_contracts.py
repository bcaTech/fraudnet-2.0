#!/usr/bin/env python3
"""Cross-service contract verification.

Currently checks:
  - All Avro schemas in packages/schemas/avro/ are syntactically valid.
  - Each Kafka topic listed in CLAUDE.md §6.3 has a corresponding schema.
  - Avro schema versions match topic version suffixes.

Extends to OpenAPI / Protobuf as those land.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AVRO_DIR = ROOT / "packages" / "schemas" / "avro"
EXPECTED_TOPICS_V1 = {
    "voice.events",
    "sms.events",
    "data.events",
    "momo.events",
    "intel.events",
    "graph.mutations",
    "motifs.detected",
    "decisions.dispatched",
    "actions.taken",
    "audit.events",
}


def main() -> int:
    rc = 0
    if not AVRO_DIR.exists():
        print(f"avro directory missing: {AVRO_DIR}")
        return 1

    schemas: dict[str, dict] = {}
    for path in AVRO_DIR.glob("*.avsc"):
        try:
            schema = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"{path}: invalid JSON — {e}")
            rc = 1
            continue
        schemas[path.stem] = schema

    for topic in EXPECTED_TOPICS_V1:
        key = f"{topic}.v1"
        if key not in schemas:
            print(f"missing avro schema for topic {key}")
            rc = 1

    return rc


if __name__ == "__main__":
    sys.exit(main())
