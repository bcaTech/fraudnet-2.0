from __future__ import annotations

from fraudnet.features.client import InMemoryFeatureStore
from fraudnet.features.snapshot import NumberFeatures, WalletFeatures


def test_number_features_round_trip_via_bins() -> None:
    nf = NumberFeatures(
        msisdn="+233241234567",
        velocity_1m=2,
        velocity_5m=8,
        velocity_1h=30,
        fanout_1h=12,
        imei_count=1,
        geo_entropy=0.42,
        sms_freq_1h=4,
        sms_template_top="hash:abc",
        last_score=0.71,
        last_score_at_ms=1_700_000_000_000,
    )
    bins = nf.to_bins()
    rt = NumberFeatures.from_bins(nf.msisdn, bins)
    assert rt == nf


def test_wallet_features_defaults() -> None:
    wf = WalletFeatures(wallet_id="W:1")
    assert wf.txn_velocity_1h == 0
    assert wf.value_p95_24h == 0.0


async def test_in_memory_store_round_trip() -> None:
    store = InMemoryFeatureStore()
    nf = NumberFeatures(msisdn="+233241234567", velocity_1m=3)
    await store.put_number(nf)
    fetched = await store.get_number("+233241234567")
    assert fetched == nf
    assert await store.get_number("+233207777777") is None
