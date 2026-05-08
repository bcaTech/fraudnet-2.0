"""Canonical event types for the Kafka spine.

Every event has:
  - `event_id`: idempotency key (UUIDv7, time-ordered).
  - `event_ts_ms`: event time, milliseconds since epoch UTC. Stream-features
    and stream-graph use this for watermarking — DO NOT pass ingest time.
  - `ingest_ts_ms`: when the ingest service wrote the event. Used for lag
    measurement and replay.
  - `source`: which integration emitted it. Useful for debugging vendor flaps.
  - `tenant_id`: which tenant the event belongs to. Default `mtn-ghana`.

Every model has a `topic` class attribute that pins the model to its Kafka
topic — the Avro registry binding uses this. Versioning is by suffix
(`*EventV1`); a breaking change introduces `V2` and a dual-publish migration.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from fraudnet.schemas.types import (
    AccountHash,
    EntityKind,
    IMEI,
    IMSI,
    LatencyTier,
    MSISDN,
    RiskScore,
    Severity,
    Subject,
    WalletId,
)


class _EventBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=8, max_length=64)
    event_ts_ms: int = Field(ge=0)
    ingest_ts_ms: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default="mtn-ghana", min_length=1)


# ---------------------------------------------------------------------------
# Voice — `voice.events.v1`
# ---------------------------------------------------------------------------


class VoiceEventV1(_EventBase):
    """Voice signaling event from a network probe.

    Vendor-neutral via the `VoiceProbeAdapter` interface in ingest-voice. The
    same shape covers SS7, Diameter, and SIP-derived events.
    """

    topic: ClassVar[str] = "voice.events.v1"

    kind: Literal["call_start", "call_end", "registration", "handover"]
    caller: MSISDN
    callee: MSISDN | None = None  # absent for registration / handover
    imsi: IMSI | None = None
    imei: IMEI | None = None
    duration_s: int | None = Field(default=None, ge=0)
    cell_id: str | None = None
    location_area_code: str | None = None
    network: Literal["2G", "3G", "4G", "5G", "VoLTE", "VoWiFi"] | None = None
    vendor_meta: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# SMS — `sms.events.v1`
# ---------------------------------------------------------------------------


class SmsEventV1(_EventBase):
    """SMS event from the SMSC.

    `body` is gated on a regulatory `purpose=fraud_prevention` claim and is
    null otherwise. URL extraction and template clustering happen downstream
    in stream-features and brain-content.

    `rcs_verified` is set when the SMSC push carries RCS metadata
    indicating the sender is an authenticated business on the RCS
    Business Messaging registry. RCS verification is platform-grade
    (not spoofable in normal conditions) so brain-* services treat
    this as a hard trust override (DECISIONS.md D-007).
    """

    topic: ClassVar[str] = "sms.events.v1"

    kind: Literal["mt", "mo", "mt_delivery_receipt"]
    sender: MSISDN
    recipient: MSISDN
    body: str | None = None  # null unless purpose claim authorises content read
    body_hash: str | None = None  # SHA-256 of the canonicalised body
    template_hash: str | None = None  # template cluster fingerprint
    short_code: str | None = None
    smsc_id: str | None = None
    rcs_verified: bool = False  # platform-authenticated RCS sender


# ---------------------------------------------------------------------------
# Data — `data.events.v1`  (DNS / IPDR; Phase 3+)
# ---------------------------------------------------------------------------


class DataEventV1(_EventBase):
    """DNS resolver / IPDR event."""

    topic: ClassVar[str] = "data.events.v1"

    kind: Literal["dns_query", "dns_response", "ipdr_session"]
    msisdn: MSISDN | None = None  # may be absent for unattributed flows
    domain: str | None = None
    rdata: str | None = None
    bytes_up: int | None = Field(default=None, ge=0)
    bytes_down: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# MoMo — `momo.events.v1`
# ---------------------------------------------------------------------------


class MoMoEventType(StrEnum):
    P2P_TRANSFER = "p2p_transfer"
    CASH_IN = "cash_in"
    CASH_OUT = "cash_out"
    BILL_PAYMENT = "bill_payment"
    MERCHANT_PAYMENT = "merchant_payment"
    BANK_TRANSFER = "bank_transfer"
    INTERNATIONAL_REMITTANCE = "international_remittance"
    REVERSAL = "reversal"


class MoMoEventV1(_EventBase):
    """MoMo wallet event.

    Event ordering within a wallet is guaranteed by partitioning on
    `sender_wallet_id` for outbound and `recipient_wallet_id` otherwise
    (handled by the kafka-client producer key extractor in ingest-momo).
    """

    topic: ClassVar[str] = "momo.events.v1"

    kind: MoMoEventType
    txn_id: str = Field(min_length=4, max_length=64)
    sender_wallet_id: WalletId | None = None
    recipient_wallet_id: WalletId | None = None
    sender_msisdn: MSISDN | None = None
    recipient_msisdn: MSISDN | None = None
    amount_minor: int = Field(ge=0)  # smallest currency unit (pesewas for GHS)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    counterparty_kind: Literal["wallet", "bank", "merchant", "agent", "external"]
    counterparty_account_hash: AccountHash | None = None  # never plaintext
    is_reversal_of: str | None = None  # txn_id of the reversed event
    channel: Literal["app", "ussd", "agent", "merchant_pos", "api"] | None = None


# ---------------------------------------------------------------------------
# Intel — `intel.events.v1`
# ---------------------------------------------------------------------------


class IntelEventV1(_EventBase):
    """External / customer / peer-telco intel event.

    Adapters live under `services/ingest-intel`; the canonical shape here is
    the union the adapter normalises to. Provenance is preserved in `source`
    and the `attribution` field.
    """

    topic: ClassVar[str] = "intel.events.v1"

    kind: Literal[
        "customer_report",
        "peer_telco_share",
        "gsma_tisac_advisory",
        "soc_indicator",
        "blocklist_entry",
    ]
    indicator_kind: EntityKind
    indicator: str  # MSISDN, URL, IMEI, etc — interpretation per indicator_kind
    confidence: float = Field(ge=0.0, le=1.0)
    attribution: str | None = None  # human-readable origin
    severity: Severity | None = None
    notes: str | None = None  # free-form, redacted on display


# ---------------------------------------------------------------------------
# Stream-graph output — `graph.mutations.v1`
# ---------------------------------------------------------------------------


class GraphMutationV1(_EventBase):
    """A control-topic event emitted whenever stream-graph mutates Memgraph.

    Other services subscribe to this for graph-aware logic without needing to
    poll Memgraph directly.
    """

    topic: ClassVar[str] = "graph.mutations.v1"

    op: Literal["upsert_node", "upsert_edge", "delete_node", "delete_edge"]
    node_kind: (
        Literal["Number", "Wallet", "Device", "Account", "Ring", "Domain", "IPEndpoint"] | None
    ) = None
    node_id: str | None = None
    edge_kind: (
        Literal[
            "CALLED",
            "SMSED",
            "SENT",
            "OWNS",
            "USED",
            "CASHED_OUT_TO",
            "MEMBER_OF",
            "QUERIED",
            "CONNECTED",
            "RESOLVED_TO",
        ]
        | None
    ) = None
    src_kind: str | None = None
    src_id: str | None = None
    dst_kind: str | None = None
    dst_id: str | None = None
    properties: dict[str, str | int | float | bool] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Brain-graph output — `motifs.detected.v1`
# ---------------------------------------------------------------------------


class MotifDetectedV1(_EventBase):
    """A known fraud motif observed in the streaming graph."""

    topic: ClassVar[str] = "motifs.detected.v1"

    motif: Literal[
        "voice_sms_momo_24h",  # the fingerprint pattern (CLAUDE.md §6.2)
        "mule_chain",
        "fan_out_collapse",
        "smishing_burst",
        "wangiri_loop",
        # Phase 3 cross-domain motifs (brain-graph §3.6)
        "voice_then_momo_30m",
        "sms_url_blocklist",
        "device_sim_wallet_fusion",
        "sim_carousel",
        "bust_out",
    ]
    members: list[Subject]
    confidence: float = Field(ge=0.0, le=1.0)
    score: RiskScore | None = None
    evidence: dict[str, str | int | float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Decisions — `decisions.dispatched.v1`
# ---------------------------------------------------------------------------


class DecisionDispatchedV1(_EventBase):
    """A decision the orchestrator has resolved, awaiting actuator pickup."""

    topic: ClassVar[str] = "decisions.dispatched.v1"

    decision_id: str
    tier: LatencyTier
    action: str  # e.g. 'volte.tag_suspected_spam', 'momo.send_with_care'
    subject: Subject
    severity: Severity
    score: RiskScore | None = None
    policy_id: str  # which YAML rule fired
    policy_version: str
    suppression_key: str | None = None  # for downstream dedup
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
