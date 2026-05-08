#!/usr/bin/env python3
"""Seed the local dev data plane with realistic sample data.

Populates:
  - Postgres: a handful of numbers, wallets, alerts, rings, users.
  - Memgraph: a small graph with a known voice→SMS→MoMo motif for end-to-end
    testing of the moat detection path.
  - Aerospike: feature snapshots for the seeded numbers.

Real data uses Ghanaian E.164 numbers (+233...). The same data factory is
exposed in packages/testing for use in unit and integration tests.

Idempotent: re-running upserts.
"""
from __future__ import annotations

import sys


def main() -> int:
    print("seed_dev_data: stub — wired up after packages/testing factories land")
    return 0


if __name__ == "__main__":
    sys.exit(main())
