"""Aerospike client wrapper.

A thin protocol around the operations FraudNet actually performs:
  - get_number / get_wallet — single-key read, sub-millisecond budget.
  - put_number / put_wallet — write feature snapshot with TTL.
  - put_score — update the scoring bins on an existing record.

Implements both an Aerospike-backed and an in-memory fake. Tests use the
in-memory fake; production uses Aerospike.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from fraudnet.features.snapshot import NumberFeatures, WalletFeatures
from fraudnet.obs import get_logger, histogram

_log = get_logger("fraudnet.features")

_READ_DURATION = histogram(
    "fraudnet_feature_read_seconds",
    "Aerospike feature-store read duration.",
    labelnames=("entity_kind",),
    buckets=(0.0001, 0.0005, 0.001, 0.002, 0.005, 0.010, 0.025, 0.050),
)
_WRITE_DURATION = histogram(
    "fraudnet_feature_write_seconds",
    "Aerospike feature-store write duration.",
    labelnames=("entity_kind",),
    buckets=(0.0001, 0.0005, 0.001, 0.002, 0.005, 0.010, 0.025, 0.050),
)

_NS = "fraudnet"
_SET_NUMBERS = "numbers"
_SET_WALLETS = "wallets"


class FeatureStore(ABC):
    @abstractmethod
    async def get_number(self, msisdn: str) -> NumberFeatures | None: ...

    @abstractmethod
    async def put_number(self, features: NumberFeatures, *, ttl_s: int = 86_400) -> None: ...

    @abstractmethod
    async def get_wallet(self, wallet_id: str) -> WalletFeatures | None: ...

    @abstractmethod
    async def put_wallet(self, features: WalletFeatures, *, ttl_s: int = 86_400) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class AerospikeFeatureStore(FeatureStore):
    """Production implementation. Single-node Aerospike for dev; multi-node in
    prod. The driver is sync; we offload to the default executor.
    """

    def __init__(self, *, hosts: list[tuple[str, int]] | None = None) -> None:
        import aerospike  # local import — Aerospike is optional in tests

        self._aerospike = aerospike
        cfg = {"hosts": hosts or [("localhost", 3010)]}
        self._client = aerospike.client(cfg).connect()

    async def get_number(self, msisdn: str) -> NumberFeatures | None:
        bins = await self._read(_SET_NUMBERS, msisdn, kind="number")
        return NumberFeatures.from_bins(msisdn, bins) if bins else None

    async def put_number(self, features: NumberFeatures, *, ttl_s: int = 86_400) -> None:
        await self._write(_SET_NUMBERS, features.msisdn, features.to_bins(), ttl_s, "number")

    async def get_wallet(self, wallet_id: str) -> WalletFeatures | None:
        bins = await self._read(_SET_WALLETS, wallet_id, kind="wallet")
        return WalletFeatures.from_bins(wallet_id, bins) if bins else None

    async def put_wallet(self, features: WalletFeatures, *, ttl_s: int = 86_400) -> None:
        await self._write(_SET_WALLETS, features.wallet_id, features.to_bins(), ttl_s, "wallet")

    async def _read(self, set_name: str, key: str, *, kind: str) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()

        def _do() -> dict[str, Any] | None:
            try:
                _meta_key, _meta, bins = self._client.get((_NS, set_name, key))
                return bins
            except self._aerospike.exception.RecordNotFound:
                return None

        with _READ_DURATION.labels(entity_kind=kind).time():
            return await loop.run_in_executor(None, _do)

    async def _write(
        self,
        set_name: str,
        key: str,
        bins: dict[str, Any],
        ttl_s: int,
        kind: str,
    ) -> None:
        loop = asyncio.get_running_loop()
        meta = {"ttl": ttl_s}

        def _do() -> None:
            self._client.put((_NS, set_name, key), bins, meta=meta)

        with _WRITE_DURATION.labels(entity_kind=kind).time():
            await loop.run_in_executor(None, _do)

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.close)


class InMemoryFeatureStore(FeatureStore):
    """Fake for unit tests."""

    def __init__(self) -> None:
        self._numbers: dict[str, NumberFeatures] = {}
        self._wallets: dict[str, WalletFeatures] = {}

    async def get_number(self, msisdn: str) -> NumberFeatures | None:
        return self._numbers.get(msisdn)

    async def put_number(self, features: NumberFeatures, *, ttl_s: int = 86_400) -> None:
        self._numbers[features.msisdn] = features

    async def get_wallet(self, wallet_id: str) -> WalletFeatures | None:
        return self._wallets.get(wallet_id)

    async def put_wallet(self, features: WalletFeatures, *, ttl_s: int = 86_400) -> None:
        self._wallets[features.wallet_id] = features

    async def close(self) -> None:
        return None
