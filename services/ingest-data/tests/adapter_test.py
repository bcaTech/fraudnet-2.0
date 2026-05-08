from __future__ import annotations

import pytest

from ingest_data.adapter import (
    DnsPushEvent,
    IpdrPushEvent,
    dns_to_canonical,
    ipdr_to_canonical,
    partition_key,
)


class TestDnsAdapter:
    def test_query_with_msisdn(self) -> None:
        push = DnsPushEvent(
            query_id="Q-1",
            event_type="QUERY",
            timestamp_ms=1_714_492_800_000,
            msisdn="0241234567",
            qname="login-momo.example.COM.",
            qtype="A",
        )
        ev = dns_to_canonical(push, source="res-1", resolver_id="res-1")
        assert ev.kind == "dns_query"
        assert ev.msisdn == "+233241234567"
        assert ev.domain == "login-momo.example.com"
        assert ev.event_id == "dns_Q-1"

    def test_response_canonicalises_rdata_ip(self) -> None:
        push = DnsPushEvent(
            event_type="RESPONSE",
            timestamp_ms=1_714_492_800_000,
            qname="example.com",
            qtype="A",
            rdata="2001:DB8::1",
            rcode="NOERROR",
        )
        ev = dns_to_canonical(push, source="res-1", resolver_id="res-1")
        assert ev.kind == "dns_response"
        assert ev.rdata == "2001:db8::1"

    def test_response_falls_back_to_domain_for_cname(self) -> None:
        push = DnsPushEvent(
            event_type="RESPONSE",
            timestamp_ms=1_714_492_800_000,
            qname="www.example.com",
            qtype="CNAME",
            rdata="origin.EXAMPLE.com.",
        )
        ev = dns_to_canonical(push, source="res-1", resolver_id="res-1")
        assert ev.rdata == "origin.example.com"

    def test_unattributed_query_is_emitted(self) -> None:
        push = DnsPushEvent(
            event_type="QUERY",
            timestamp_ms=1_714_492_800_000,
            qname="suspect.example.com",
        )
        ev = dns_to_canonical(push, source="res-1", resolver_id="res-1")
        assert ev.msisdn is None
        assert ev.domain == "suspect.example.com"
        assert ev.event_id.startswith("dns_")

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown DNS event_type"):
            dns_to_canonical(
                DnsPushEvent(
                    event_type="GIBBERISH",
                    timestamp_ms=1,
                    qname="example.com",
                ),
                source="res-1",
                resolver_id="res-1",
            )

    def test_partition_key_prefers_msisdn(self) -> None:
        push = DnsPushEvent(
            event_type="QUERY",
            timestamp_ms=1,
            msisdn="0241234567",
            qname="example.com",
        )
        ev = dns_to_canonical(push, source="r", resolver_id="r")
        assert partition_key(ev) == "+233241234567"

    def test_partition_key_falls_back_to_domain(self) -> None:
        push = DnsPushEvent(event_type="QUERY", timestamp_ms=1, qname="example.com")
        ev = dns_to_canonical(push, source="r", resolver_id="r")
        assert partition_key(ev) == "d:example.com"


class TestIpdrAdapter:
    def test_session_with_domain_and_ip(self) -> None:
        push = IpdrPushEvent(
            session_id="S-1",
            timestamp_ms=1_714_492_800_000,
            msisdn="0241234567",
            dst_domain="CDN.example.com.",
            dst_ip="203.0.113.42",
            bytes_up=100,
            bytes_down=2_000,
            duration_s=12,
        )
        ev = ipdr_to_canonical(push, source="ipdr-1", collector_id="ipdr-1")
        assert ev.kind == "ipdr_session"
        assert ev.msisdn == "+233241234567"
        assert ev.domain == "cdn.example.com"
        assert ev.rdata == "203.0.113.42"
        assert ev.bytes_up == 100
        assert ev.bytes_down == 2_000
        assert ev.event_id == "ipdr_S-1"

    def test_requires_destination(self) -> None:
        with pytest.raises(ValueError, match="ipdr requires dst_domain or dst_ip"):
            ipdr_to_canonical(
                IpdrPushEvent(
                    timestamp_ms=1,
                    msisdn="0241234567",
                    bytes_up=0,
                    bytes_down=0,
                ),
                source="ipdr-1",
                collector_id="ipdr-1",
            )

    def test_partition_key_uses_msisdn(self) -> None:
        ev = ipdr_to_canonical(
            IpdrPushEvent(
                timestamp_ms=1,
                msisdn="0241234567",
                dst_ip="203.0.113.42",
                bytes_up=0,
                bytes_down=0,
            ),
            source="ipdr-1",
            collector_id="ipdr-1",
        )
        assert partition_key(ev) == "+233241234567"
