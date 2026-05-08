"""Aerospike feature store client.

CLAUDE.md §6.4: keys are entity IDs, single-key reads only, no scans on the
hot path. The 1 ms p99 read budget is the entire reason Aerospike was chosen
over Redis.

Layout (mirrors §6.4):

    Namespace: fraudnet
    Set:       numbers       Key: msisdn
    Set:       wallets       Key: wallet_id
    Set:       devices       Key: imei
    Set:       urls          Key: url

Bins are typed strings; producers and consumers use the FeatureSnapshot
dataclass to keep the field set in lockstep.
"""

from fraudnet.features.client import (
    AerospikeFeatureStore,
    FeatureStore,
    InMemoryFeatureStore,
)
from fraudnet.features.snapshot import (
    FeatureSnapshot,
    NumberFeatures,
    WalletFeatures,
)

__all__ = [
    "AerospikeFeatureStore",
    "FeatureSnapshot",
    "FeatureStore",
    "InMemoryFeatureStore",
    "NumberFeatures",
    "WalletFeatures",
]
