"""FeaturePipeline tests.

Pure-function tests on the per-key sliding-window logic. No Kafka, no
Aerospike. Same logic ports to the PyFlink Table-API wrapper.
"""

from __future__ import annotations

from fraudnet.testing.factories import make_momo_event, make_sms_event, make_voice_event
from stream_features.pipeline import FeaturePipeline


def test_voice_velocity_1m_5m_1h() -> None:
    p = FeaturePipeline()
    base = 1_700_000_000_000
    caller = "+233241234567"
    # 3 calls in last 30 s, 5 more in last 4 min, 10 more in last 30 min
    for i in range(3):
        p.feed_voice(make_voice_event(caller=caller, callee=f"+23320700{i:04d}",
                                      event_ts_ms=base + (i * 10_000)))
    for i in range(5):
        p.feed_voice(make_voice_event(caller=caller, callee=f"+23320710{i:04d}",
                                      event_ts_ms=base + 40_000 + (i * 30_000)))
    for i in range(10):
        p.feed_voice(make_voice_event(caller=caller, callee=f"+23320720{i:04d}",
                                      event_ts_ms=base + 200_000 + (i * 120_000)))

    nf = p.number_features(caller)
    # 1m window — only the last few events
    assert nf.velocity_1h == 18  # all events fit in last hour
    assert nf.velocity_5m >= 8
    assert nf.fanout_1h == 18  # 18 unique callees


def test_voice_late_event_dropped() -> None:
    p = FeaturePipeline(watermark_lateness_ms=30_000)
    base = 1_700_000_000_000
    caller = "+233241234567"

    p.feed_voice(make_voice_event(caller=caller, callee="+233207000001", event_ts_ms=base + 100_000))
    # 50s late — dropped
    p.feed_voice(make_voice_event(caller=caller, callee="+233207000002", event_ts_ms=base + 50_000))

    nf = p.number_features(caller)
    assert nf.velocity_1h == 1
    assert p.late_events_dropped == 1


def test_imei_churn() -> None:
    p = FeaturePipeline()
    base = 1_700_000_000_000
    caller = "+233241234567"
    for i, imei in enumerate(["111111111111111", "222222222222222", "333333333333333"]):
        p.feed_voice(
            make_voice_event(
                caller=caller,
                callee="+233207654321",
                imei=imei,
                event_ts_ms=base + i * 1000,
            )
        )
    assert p.number_features(caller).imei_count == 3


def test_sms_template_top() -> None:
    p = FeaturePipeline()
    base = 1_700_000_000_000
    sender = "+233241234567"
    # template hash A appears 3 times, B once
    for i in range(3):
        p.feed_sms(make_sms_event(sender=sender, recipient=f"+23320700{i:04d}",
                                  template_hash="sha256:A", event_ts_ms=base + i * 1000))
    p.feed_sms(make_sms_event(sender=sender, recipient="+233207654321",
                              template_hash="sha256:B", event_ts_ms=base + 4000))
    nf = p.number_features(sender)
    assert nf.sms_template_top == "sha256:A"
    assert nf.sms_freq_1h == 4


def test_momo_velocity_and_diversity() -> None:
    p = FeaturePipeline()
    base = 1_700_000_000_000
    sender_wallet = "W:233241234567"
    # 5 outbound transfers to 3 unique counterparties
    for i in range(5):
        p.feed_momo(make_momo_event(
            sender_wallet_id=sender_wallet,
            recipient_wallet_id=f"W:cp{i % 3}",
            event_ts_ms=base + i * 1000,
        ))
    wf = p.wallet_features(sender_wallet)
    assert wf.txn_velocity_1h == 5
    assert wf.counterparty_diversity_24h == 3


def test_momo_with_no_wallet_id_returns_none() -> None:
    p = FeaturePipeline()
    base = 1_700_000_000_000
    out = p.feed_momo(make_momo_event(
        sender_wallet_id=None,
        recipient_wallet_id=None,
        event_ts_ms=base,
    ))
    assert out is None


def test_unknown_number_returns_zero_features() -> None:
    p = FeaturePipeline()
    nf = p.number_features("+233244444444")
    assert nf.velocity_1h == 0
    assert nf.imei_count == 0


def test_pruning_drops_old_calls() -> None:
    p = FeaturePipeline()
    base = 1_700_000_000_000
    caller = "+233241234567"
    # call 2h ago
    p.feed_voice(make_voice_event(caller=caller, callee="+233207000001", event_ts_ms=base))
    # call now (2h later)
    p.feed_voice(make_voice_event(caller=caller, callee="+233207000002",
                                  event_ts_ms=base + 2 * 60 * 60 * 1000))

    nf = p.number_features(caller)
    assert nf.velocity_1h == 1  # only the recent one
