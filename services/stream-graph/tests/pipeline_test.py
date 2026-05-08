"""stream-graph pipeline tests — pure functions, no Memgraph or Kafka."""

from __future__ import annotations

from fraudnet.schemas.events import MoMoEventType
from fraudnet.testing.factories import (
    make_data_event,
    make_momo_event,
    make_sms_event,
    make_voice_event,
)
from stream_graph.pipeline import translate_data, translate_momo, translate_sms, translate_voice


class TestTranslateVoice:
    def test_call_start_creates_caller_callee_called_edge(self) -> None:
        ev = make_voice_event(
            caller="0241234567",
            callee="0207654321",
            duration_s=42,
        )
        ops = translate_voice(ev)
        kinds = [(o.op, o.node_kind, o.edge_kind) for o in ops]
        assert ("upsert_node", "Number", None) in kinds  # caller upsert
        assert sum(1 for o in ops if o.op == "upsert_node" and o.node_kind == "Number") >= 2
        edges = [o for o in ops if o.op == "upsert_edge"]
        assert any(e.edge_kind == "CALLED" for e in edges)

    def test_imei_creates_device_node_and_used_edge(self) -> None:
        ev = make_voice_event(imei="111111111111111")
        ops = translate_voice(ev)
        assert any(o.op == "upsert_node" and o.node_kind == "Device" for o in ops)
        assert any(o.op == "upsert_edge" and o.edge_kind == "USED" for o in ops)

    def test_call_end_does_not_create_called_edge(self) -> None:
        ev = make_voice_event(kind="call_end")
        ops = translate_voice(ev)
        assert not any(o.edge_kind == "CALLED" for o in ops)


class TestTranslateSms:
    def test_mt_creates_smsed_edge(self) -> None:
        ev = make_sms_event(kind="mt", template_hash="sha256:abc")
        ops = translate_sms(ev)
        edges = [o for o in ops if o.op == "upsert_edge"]
        assert len(edges) == 1
        assert edges[0].edge_kind == "SMSED"
        assert edges[0].properties["template_hash"] == "sha256:abc"

    def test_dr_creates_no_edge(self) -> None:
        ev = make_sms_event(kind="mt_delivery_receipt")
        ops = translate_sms(ev)
        assert not any(o.op == "upsert_edge" for o in ops)


class TestTranslateMoMo:
    def test_p2p_creates_owns_and_sent_edges(self) -> None:
        ev = make_momo_event(kind=MoMoEventType.P2P_TRANSFER)
        ops = translate_momo(ev)
        edges = [o for o in ops if o.op == "upsert_edge"]
        assert any(e.edge_kind == "OWNS" for e in edges)
        sent = [e for e in edges if e.edge_kind == "SENT"]
        assert len(sent) == 1
        assert sent[0].src_kind == "Wallet"

    def test_reversal_no_sent_edge(self) -> None:
        ev = make_momo_event(kind=MoMoEventType.REVERSAL, is_reversal_of="OTHER-TXN")
        ops = translate_momo(ev)
        assert not any(o.edge_kind == "SENT" for o in ops)

    def test_cash_in_no_sent_edge(self) -> None:
        ev = make_momo_event(kind=MoMoEventType.CASH_IN, sender_wallet_id=None)
        ops = translate_momo(ev)
        assert not any(o.edge_kind == "SENT" for o in ops)

    def test_cash_out_to_bank_creates_account_node_and_edge(self) -> None:
        ev = make_momo_event(
            kind=MoMoEventType.CASH_OUT,
            recipient_wallet_id=None,
            counterparty_kind="bank",
            counterparty_account_hash="hash:bank-account-1",
        )
        ops = translate_momo(ev)
        assert any(o.op == "upsert_node" and o.node_kind == "Account" for o in ops)
        assert any(o.op == "upsert_edge" and o.edge_kind == "CASHED_OUT_TO" for o in ops)


class TestTranslateData:
    def test_dns_query_creates_queried_edge(self) -> None:
        ev = make_data_event(
            kind="dns_query",
            msisdn="0241234567",
            domain="phish.example.com",
        )
        ops = translate_data(ev)
        assert any(o.op == "upsert_node" and o.node_kind == "Number" for o in ops)
        assert any(o.op == "upsert_node" and o.node_kind == "Domain" for o in ops)
        edges = [o for o in ops if o.op == "upsert_edge"]
        assert len(edges) == 1
        assert edges[0].edge_kind == "QUERIED"
        assert edges[0].src_kind == "Number" and edges[0].dst_kind == "Domain"

    def test_dns_response_with_ip_creates_resolved_to_edge(self) -> None:
        ev = make_data_event(
            kind="dns_response",
            msisdn="0241234567",
            domain="phish.example.com",
            rdata="203.0.113.42",
        )
        ops = translate_data(ev)
        edges = [o for o in ops if o.op == "upsert_edge"]
        kinds = {e.edge_kind for e in edges}
        assert "QUERIED" in kinds
        assert "RESOLVED_TO" in kinds
        assert any(o.op == "upsert_node" and o.node_kind == "IPEndpoint" for o in ops)

    def test_ipdr_session_with_domain_creates_connected_edge(self) -> None:
        ev = make_data_event(
            kind="ipdr_session",
            msisdn="0241234567",
            domain="cdn.example.com",
            rdata=None,
            bytes_up=1000,
            bytes_down=10000,
        )
        ops = translate_data(ev)
        edges = [o for o in ops if o.op == "upsert_edge"]
        assert len(edges) == 1
        assert edges[0].edge_kind == "CONNECTED"
        assert edges[0].dst_kind == "Domain"
        assert edges[0].properties["bytes_up"] == 1000
        assert edges[0].properties["bytes_down"] == 10000

    def test_ipdr_session_ip_only_falls_back_to_ipendpoint(self) -> None:
        ev = make_data_event(
            kind="ipdr_session",
            msisdn="0241234567",
            domain=None,
            rdata="203.0.113.42",
            bytes_up=100,
            bytes_down=200,
        )
        ops = translate_data(ev)
        assert any(o.op == "upsert_node" and o.node_kind == "IPEndpoint" for o in ops)
        edges = [o for o in ops if o.op == "upsert_edge"]
        assert edges[0].edge_kind == "CONNECTED"
        assert edges[0].dst_kind == "IPEndpoint"

    def test_unattributed_dns_does_not_create_edge(self) -> None:
        ev = make_data_event(
            kind="dns_query",
            msisdn=None,
            domain="suspect.example.com",
        )
        ops = translate_data(ev)
        # Domain node still created so reputation aggregation works.
        assert any(o.op == "upsert_node" and o.node_kind == "Domain" for o in ops)
        # No QUERIED edge — we don't have a Number on this side.
        assert not any(o.op == "upsert_edge" and o.edge_kind == "QUERIED" for o in ops)


def test_op_to_mutation_round_trip() -> None:
    ev = make_voice_event()
    ops = translate_voice(ev)
    op = ops[0]
    mutation = op.to_mutation(
        event_id=ev.event_id,
        event_ts_ms=ev.event_ts_ms,
        ingest_ts_ms=ev.ingest_ts_ms,
        source="t",
    )
    assert mutation.topic == "graph.mutations.v1"
    assert mutation.op == op.op
