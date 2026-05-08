"""Per-event translator: domain event → list of graph mutations + control-topic events.

Pure functions; no Memgraph or Kafka imports. The same logic ports to the
Phase-2 PyFlink job. CLAUDE.md §6.2 lists the node and edge types we maintain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from fraudnet.schemas.events import (
    DataEventV1,
    GraphMutationV1,
    MoMoEventType,
    MoMoEventV1,
    SmsEventV1,
    VoiceEventV1,
)


@dataclass(frozen=True)
class GraphOp:
    """One mutation, ready for the BufferedGraphWriter or a control-topic event.

    `op` mirrors the GraphMutationV1.op enum; populated fields differ by op:
      - upsert_node: node_kind + node_id + properties
      - upsert_edge: edge_kind + (src_kind, src_id) + (dst_kind, dst_id) + properties
    """

    op: Literal["upsert_node", "upsert_edge"]
    node_kind: str | None = None
    node_id: str | None = None
    edge_kind: str | None = None
    src_kind: str | None = None
    src_id: str | None = None
    dst_kind: str | None = None
    dst_id: str | None = None
    properties: dict[str, str | int | float | bool] = field(default_factory=dict)

    def to_mutation(
        self,
        *,
        event_id: str,
        event_ts_ms: int,
        ingest_ts_ms: int,
        source: str,
        tenant_id: str = "mtn-ghana",
    ) -> GraphMutationV1:
        return GraphMutationV1(
            event_id=f"gm_{event_id[:32]}_{self.op[:4]}",
            event_ts_ms=event_ts_ms,
            ingest_ts_ms=ingest_ts_ms,
            source=source,
            tenant_id=tenant_id,
            op=self.op,
            node_kind=self.node_kind,
            node_id=self.node_id,
            edge_kind=self.edge_kind,  # type: ignore[arg-type]
            src_kind=self.src_kind,
            src_id=self.src_id,
            dst_kind=self.dst_kind,
            dst_id=self.dst_id,
            properties=self.properties,
        )


# ---------------------------------------------------------------------------
# Translators
# ---------------------------------------------------------------------------


def translate_voice(ev: VoiceEventV1) -> list[GraphOp]:
    """Voice event → MERGE caller, MERGE callee (if any), CREATE :CALLED edge.
    If IMEI present, MERGE Device + USED edge.
    """
    out: list[GraphOp] = [
        GraphOp(op="upsert_node", node_kind="Number", node_id=ev.caller),
    ]
    if ev.callee:
        out.append(GraphOp(op="upsert_node", node_kind="Number", node_id=ev.callee))
    if ev.kind == "call_start" and ev.callee:
        out.append(
            GraphOp(
                op="upsert_edge",
                edge_kind="CALLED",
                src_kind="Number",
                src_id=ev.caller,
                dst_kind="Number",
                dst_id=ev.callee,
                properties={
                    "ts": ev.event_ts_ms,
                    "duration": ev.duration_s or 0,
                },
            )
        )
    if ev.imei:
        out.append(GraphOp(op="upsert_node", node_kind="Device", node_id=ev.imei))
        out.append(
            GraphOp(
                op="upsert_edge",
                edge_kind="USED",
                src_kind="Number",
                src_id=ev.caller,
                dst_kind="Device",
                dst_id=ev.imei,
                properties={"since": ev.event_ts_ms},
            )
        )
    return out


def translate_sms(ev: SmsEventV1) -> list[GraphOp]:
    out: list[GraphOp] = [
        GraphOp(op="upsert_node", node_kind="Number", node_id=ev.sender),
        GraphOp(op="upsert_node", node_kind="Number", node_id=ev.recipient),
    ]
    if ev.kind in {"mt", "mo"}:
        props: dict[str, str | int | float | bool] = {"ts": ev.event_ts_ms}
        if ev.template_hash:
            props["template_hash"] = ev.template_hash
        out.append(
            GraphOp(
                op="upsert_edge",
                edge_kind="SMSED",
                src_kind="Number",
                src_id=ev.sender,
                dst_kind="Number",
                dst_id=ev.recipient,
                properties=props,
            )
        )
    return out


def translate_data(ev: DataEventV1) -> list[GraphOp]:
    """Data event → MERGE Number / Domain / IPEndpoint, CREATE :QUERIED / :CONNECTED.

    DNS query: (:Number)-[:QUERIED]->(:Domain).
    DNS response with IP rdata: also emit (:Domain)-[:RESOLVED_TO]->(:IPEndpoint).
    IPDR session: (:Number)-[:CONNECTED]->(:Domain | :IPEndpoint) with bytes.
    """
    out: list[GraphOp] = []

    msisdn = ev.msisdn
    if msisdn:
        out.append(GraphOp(op="upsert_node", node_kind="Number", node_id=msisdn))

    domain = ev.domain
    if domain:
        out.append(GraphOp(op="upsert_node", node_kind="Domain", node_id=domain))

    if ev.kind in {"dns_query", "dns_response"}:
        if msisdn and domain:
            out.append(
                GraphOp(
                    op="upsert_edge",
                    edge_kind="QUERIED",
                    src_kind="Number",
                    src_id=msisdn,
                    dst_kind="Domain",
                    dst_id=domain,
                    properties={"ts": ev.event_ts_ms, "kind": ev.kind},
                )
            )
        # On a DNS response the rdata can be an IP — record the resolution
        # edge so brain-graph can pivot from a flagged domain to all IPs
        # serving it (and vice versa).
        if ev.kind == "dns_response" and ev.rdata and domain:
            ip = ev.rdata
            if _looks_like_ip(ip):
                out.append(GraphOp(op="upsert_node", node_kind="IPEndpoint", node_id=ip))
                out.append(
                    GraphOp(
                        op="upsert_edge",
                        edge_kind="RESOLVED_TO",
                        src_kind="Domain",
                        src_id=domain,
                        dst_kind="IPEndpoint",
                        dst_id=ip,
                        properties={"ts": ev.event_ts_ms},
                    )
                )

    if ev.kind == "ipdr_session" and msisdn:
        # Prefer domain attribution; fall back to IP. Connection edge carries
        # bytes for downstream volume-anomaly aggregation.
        dst_kind: str | None = None
        dst_id: str | None = None
        if domain:
            dst_kind, dst_id = "Domain", domain
        elif ev.rdata:
            dst_kind, dst_id = "IPEndpoint", ev.rdata
            out.append(GraphOp(op="upsert_node", node_kind="IPEndpoint", node_id=ev.rdata))
        if dst_kind and dst_id:
            out.append(
                GraphOp(
                    op="upsert_edge",
                    edge_kind="CONNECTED",
                    src_kind="Number",
                    src_id=msisdn,
                    dst_kind=dst_kind,
                    dst_id=dst_id,
                    properties={
                        "ts": ev.event_ts_ms,
                        "bytes_up": ev.bytes_up or 0,
                        "bytes_down": ev.bytes_down or 0,
                    },
                )
            )

    return out


def _looks_like_ip(s: str) -> bool:
    # Tight enough — adapter has already canonicalised; this is just to
    # avoid emitting an IPEndpoint for a CNAME-style rdata.
    return ":" in s or (s.count(".") == 3 and all(p.isdigit() for p in s.split(".")))


def translate_momo(ev: MoMoEventV1) -> list[GraphOp]:
    """MoMo event → MERGE wallet(s), CREATE :SENT edge for transfers,
    OWNS edge linking msisdn ↔ wallet, CASHED_OUT_TO for bank/external.
    """
    out: list[GraphOp] = []

    # Sender side
    if ev.sender_wallet_id:
        out.append(GraphOp(op="upsert_node", node_kind="Wallet", node_id=ev.sender_wallet_id))
        if ev.sender_msisdn:
            out.append(GraphOp(op="upsert_node", node_kind="Number", node_id=ev.sender_msisdn))
            out.append(
                GraphOp(
                    op="upsert_edge",
                    edge_kind="OWNS",
                    src_kind="Number",
                    src_id=ev.sender_msisdn,
                    dst_kind="Wallet",
                    dst_id=ev.sender_wallet_id,
                )
            )

    # Recipient side
    if ev.recipient_wallet_id:
        out.append(GraphOp(op="upsert_node", node_kind="Wallet", node_id=ev.recipient_wallet_id))
        if ev.recipient_msisdn:
            out.append(GraphOp(op="upsert_node", node_kind="Number", node_id=ev.recipient_msisdn))
            out.append(
                GraphOp(
                    op="upsert_edge",
                    edge_kind="OWNS",
                    src_kind="Number",
                    src_id=ev.recipient_msisdn,
                    dst_kind="Wallet",
                    dst_id=ev.recipient_wallet_id,
                )
            )

    # Money-flow edge
    if ev.kind not in {MoMoEventType.REVERSAL, MoMoEventType.CASH_IN}:
        if ev.sender_wallet_id and ev.recipient_wallet_id:
            out.append(
                GraphOp(
                    op="upsert_edge",
                    edge_kind="SENT",
                    src_kind="Wallet",
                    src_id=ev.sender_wallet_id,
                    dst_kind="Wallet",
                    dst_id=ev.recipient_wallet_id,
                    properties={"ts": ev.event_ts_ms, "amount": ev.amount_minor},
                )
            )
        elif ev.sender_wallet_id and ev.counterparty_account_hash and ev.counterparty_kind in {
            "bank",
            "external",
        }:
            # Cash-out to bank / external
            out.append(
                GraphOp(
                    op="upsert_node",
                    node_kind="Account",
                    node_id=ev.counterparty_account_hash,
                )
            )
            out.append(
                GraphOp(
                    op="upsert_edge",
                    edge_kind="CASHED_OUT_TO",
                    src_kind="Wallet",
                    src_id=ev.sender_wallet_id,
                    dst_kind="Account",
                    dst_id=ev.counterparty_account_hash,
                    properties={"ts": ev.event_ts_ms, "amount": ev.amount_minor},
                )
            )

    return out
