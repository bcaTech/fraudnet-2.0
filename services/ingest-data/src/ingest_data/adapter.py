"""DNS + IPDR adapters.

Both adapters land on `DataEventV1` (`data.events.v1`). Vendor differences
are kept inside the `*PushEvent` shapes; we never widen the canonical
event for vendor quirks.

Partitioning policy
-------------------
For events with an MSISDN, we partition on the MSISDN — this groups a
subscriber's DNS / data activity together so stream-features can compute
per-subscriber velocity in a single Flink task. For events without an
MSISDN (unattributed DNS), we partition on the canonical domain so
domain-reputation aggregations co-locate too.
"""

from __future__ import annotations

import hashlib
from time import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fraudnet.schemas.events import DataEventV1
from fraudnet.schemas.types import MSISDN
from ingest_data.normaliser import canonicalise_domain, canonicalise_ip

DnsKind = Literal["dns_query", "dns_response"]


class DnsPushEvent(BaseModel):
    """DNS resolver push payload.

    Fields kept minimal — anything richer (resolution chain, ECS subnet,
    DNSSEC validation status) is vendor-extensible via `model_extra` and
    surfaced into stream-features only when there is a use case.
    """

    model_config = ConfigDict(extra="allow")

    query_id: str | None = None
    event_type: str = Field(min_length=1, max_length=32)  # 'query' | 'response'
    timestamp_ms: int = Field(ge=0)
    msisdn: str | None = None  # resolver-side attribution; may be absent
    qname: str = Field(min_length=1, max_length=253)
    qtype: str | None = Field(default=None, max_length=16)  # 'A' | 'AAAA' | 'CNAME' | ...
    rdata: str | None = None  # resolved IP/CNAME on response events
    resolver_id: str | None = None
    rcode: str | None = None  # 'NOERROR' | 'NXDOMAIN' | ...


class IpdrPushEvent(BaseModel):
    """IP detail record push payload.

    IPDRs come from DPI / packet brokers; we only retain the fields needed
    for fraud signal computation (volume anomalies, suspicious destinations,
    short-lived high-volume flows).
    """

    model_config = ConfigDict(extra="allow")

    session_id: str | None = None
    timestamp_ms: int = Field(ge=0)
    msisdn: str  # IPDR is always subscriber-attributed
    dst_domain: str | None = None  # DPI-derived host where available
    dst_ip: str | None = None
    bytes_up: int = Field(ge=0)
    bytes_down: int = Field(ge=0)
    duration_s: int | None = Field(default=None, ge=0)
    collector_id: str | None = None


_DNS_KIND_MAP: dict[str, DnsKind] = {
    "QUERY": "dns_query",
    "Q": "dns_query",
    "RESPONSE": "dns_response",
    "R": "dns_response",
    "DNS_QUERY": "dns_query",
    "DNS_RESPONSE": "dns_response",
}


def dns_to_canonical(
    raw: DnsPushEvent,
    *,
    source: str,
    resolver_id: str,
    tenant_id: str = "mtn-ghana",
    event_id: str | None = None,
) -> DataEventV1:
    kind = _DNS_KIND_MAP.get(raw.event_type.upper())
    if kind is None:
        raise ValueError(f"unknown DNS event_type: {raw.event_type!r}")

    domain = canonicalise_domain(raw.qname).fqdn
    rdata: str | None = None
    if raw.rdata:
        # rdata can be an IP (A/AAAA) or another domain (CNAME). Try IP first
        # for tighter validation; fall back to domain canonicalisation.
        try:
            rdata = canonicalise_ip(raw.rdata)
        except ValueError:
            try:
                rdata = canonicalise_domain(raw.rdata).fqdn
            except ValueError:
                rdata = raw.rdata.strip().lower()

    return DataEventV1(
        event_id=event_id or _derive_dns_event_id(raw, domain),
        event_ts_ms=raw.timestamp_ms,
        ingest_ts_ms=int(time() * 1000),
        source=source,
        tenant_id=tenant_id,
        kind=kind,
        msisdn=MSISDN(raw.msisdn) if raw.msisdn else None,
        domain=domain,
        rdata=rdata,
        bytes_up=None,
        bytes_down=None,
    )


def ipdr_to_canonical(
    raw: IpdrPushEvent,
    *,
    source: str,
    collector_id: str,
    tenant_id: str = "mtn-ghana",
    event_id: str | None = None,
) -> DataEventV1:
    msisdn = MSISDN(raw.msisdn)

    domain: str | None = None
    if raw.dst_domain:
        domain = canonicalise_domain(raw.dst_domain).fqdn

    rdata: str | None = None
    if raw.dst_ip:
        rdata = canonicalise_ip(raw.dst_ip)

    if domain is None and rdata is None:
        raise ValueError("ipdr requires dst_domain or dst_ip")

    return DataEventV1(
        event_id=event_id or _derive_ipdr_event_id(raw, domain or rdata or ""),
        event_ts_ms=raw.timestamp_ms,
        ingest_ts_ms=int(time() * 1000),
        source=source,
        tenant_id=tenant_id,
        kind="ipdr_session",
        msisdn=msisdn,
        domain=domain,
        rdata=rdata,
        bytes_up=raw.bytes_up,
        bytes_down=raw.bytes_down,
    )


def partition_key(event: DataEventV1) -> str:
    """Co-locate by subscriber when attributed; by domain otherwise."""
    if event.msisdn:
        return str(event.msisdn)
    if event.domain:
        return f"d:{event.domain}"
    return event.event_id


def _derive_dns_event_id(raw: DnsPushEvent, canonical_domain: str) -> str:
    if raw.query_id:
        return f"dns_{raw.query_id[:32]}"
    natural = (
        f"{raw.msisdn or '-'}|{canonical_domain}|{raw.event_type}|{raw.timestamp_ms}"
    ).encode()
    return f"dns_{hashlib.sha256(natural).hexdigest()[:24]}"


def _derive_ipdr_event_id(raw: IpdrPushEvent, target: str) -> str:
    if raw.session_id:
        return f"ipdr_{raw.session_id[:32]}"
    natural = f"{raw.msisdn}|{target}|{raw.timestamp_ms}".encode()
    return f"ipdr_{hashlib.sha256(natural).hexdigest()[:24]}"
