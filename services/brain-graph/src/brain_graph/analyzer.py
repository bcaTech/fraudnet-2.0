"""Top-level analysis runner.

`Analyzer.run_once()` performs the full batch:
  1. Extract a recent slice from Memgraph.
  2. Run all motif detectors over the slice.
  3. Run community detection.
  4. Identify rings (connected components scored against motif density).
  5. Publish MotifDetectedV1 events for each motif match.

The analyzer is purpose-claim aware: it acquires `Purpose.FRAUD_PREVENTION`
for the duration of the batch (graph reads require this, audit-lib §7.2).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

from fraudnet.audit import with_purpose
from fraudnet.graph import GraphClient, GraphScope
from fraudnet.kafka import AvroProducer
from fraudnet.obs import counter, get_logger, histogram
from fraudnet.schemas.events import MotifDetectedV1
from fraudnet.schemas.types import EntityKind, Purpose, RiskScore, Subject

from brain_graph.community import Community, detect_communities
from brain_graph.motifs import (
    MotifMatch,
    detect_bust_outs,
    detect_device_sim_wallet_fusion,
    detect_mule_chains,
    detect_sim_carousels,
    detect_sms_url_blocklist,
    detect_voice_sms_momo_24h,
    detect_voice_then_momo_30m,
)
from brain_graph.rings import RingCandidate, identify_rings
from brain_graph.subgraph import Subgraph, extract_window

_log = get_logger("brain_graph.analyzer")

_BATCH_DURATION = histogram(
    "brain_graph_batch_seconds",
    "End-to-end brain-graph batch duration.",
    labelnames=("phase",),
)
_MOTIFS_FOUND = counter(
    "brain_graph_motifs_found_total",
    "Motif matches discovered.",
    labelnames=("motif",),
)
_RINGS_FOUND = counter(
    "brain_graph_rings_found_total",
    "Ring candidates discovered.",
)
_COMMUNITIES_FOUND = counter(
    "brain_graph_communities_found_total",
    "Communities of size >= min_size detected.",
)


_NODE_KIND_TO_ENTITY_KIND: dict[str, EntityKind] = {
    "Number": EntityKind.NUMBER,
    "Wallet": EntityKind.WALLET,
    "Device": EntityKind.DEVICE,
    "Account": EntityKind.ACCOUNT,
    "Domain": EntityKind.URL,  # Subject for OTT motifs
}


@dataclass(frozen=True)
class AnalysisResult:
    extracted_at_ms: int
    node_count: int
    edge_count: int
    motifs: tuple[MotifMatch, ...]
    communities: tuple[Community, ...]
    rings: tuple[RingCandidate, ...]


class Analyzer:
    def __init__(
        self,
        *,
        graph_client: GraphClient,
        motif_producer: AvroProducer[MotifDetectedV1],
        tenant_id: str = "mtn-ghana",
        window_hours: int = 24,
        max_nodes: int = 5000,
        flagged_domains: frozenset[str] | None = None,
    ) -> None:
        self._graph = graph_client
        self._producer = motif_producer
        self._tenant_id = tenant_id
        self._window_hours = window_hours
        self._max_nodes = max_nodes
        # Domains that brain-content / url-intel have flagged. Hot-loaded
        # by reference; the runtime can swap the set without rebuilding the
        # analyser. Empty disables sms_url_blocklist detection.
        self._flagged_domains = flagged_domains or frozenset()

    @property
    def flagged_domains(self) -> frozenset[str]:
        return self._flagged_domains

    def update_flagged_domains(self, domains: frozenset[str]) -> None:
        self._flagged_domains = domains

    async def run_once(self) -> AnalysisResult:
        scope = GraphScope(tenant_id=self._tenant_id)
        with with_purpose(Purpose.FRAUD_PREVENTION):
            with _BATCH_DURATION.labels(phase="extract").time():
                async with self._graph.session(scope) as session:
                    floor_ms = int((time.time() - self._window_hours * 3600) * 1000)
                    sg = await extract_window(
                        session,
                        tenant_id=self._tenant_id,
                        window_floor_ms=floor_ms,
                        max_nodes=self._max_nodes,
                    )

        with _BATCH_DURATION.labels(phase="analyse").time():
            motifs = self._run_motifs(sg)
            communities = tuple(detect_communities(sg))
            rings = tuple(identify_rings(sg, list(motifs)))

        with _BATCH_DURATION.labels(phase="publish").time():
            await self._publish_motifs(motifs)

        _RINGS_FOUND.inc(len(rings))
        _COMMUNITIES_FOUND.inc(len(communities))
        for m in motifs:
            _MOTIFS_FOUND.labels(motif=m.motif).inc()
        _log.info(
            "brain_graph.batch_complete",
            nodes=len(sg.nodes),
            edges=len(sg.edges),
            motif_count=len(motifs),
            community_count=len(communities),
            ring_count=len(rings),
        )
        return AnalysisResult(
            extracted_at_ms=int(time.time() * 1000),
            node_count=len(sg.nodes),
            edge_count=len(sg.edges),
            motifs=tuple(motifs),
            communities=communities,
            rings=rings,
        )

    def _run_motifs(self, sg: Subgraph) -> list[MotifMatch]:
        out: list[MotifMatch] = []
        out.extend(detect_voice_sms_momo_24h(sg))
        out.extend(detect_mule_chains(sg))
        out.extend(detect_sim_carousels(sg))
        out.extend(detect_bust_outs(sg))
        # Phase 3 cross-domain motifs.
        out.extend(detect_voice_then_momo_30m(sg))
        out.extend(detect_sms_url_blocklist(sg, flagged_domains=self._flagged_domains))
        out.extend(detect_device_sim_wallet_fusion(sg))
        return out

    async def _publish_motifs(self, motifs: list[MotifMatch]) -> None:
        for match in motifs:
            event = _to_motif_event(match, tenant_id=self._tenant_id)
            await self._producer.send(event, key=event.members[0].id if event.members else None)


def _to_motif_event(match: MotifMatch, *, tenant_id: str) -> MotifDetectedV1:
    now_ms = int(time.time() * 1000)
    members: list[Subject] = []
    for kind, member_id in match.members:
        ent = _NODE_KIND_TO_ENTITY_KIND.get(kind)
        if ent is None:
            continue
        members.append(Subject(kind=ent, id=member_id))
    score = RiskScore(
        value=float(match.confidence),
        model_id="brain-graph-motif",
        model_version="0.2.0",
        computed_at_ms=now_ms,
    )
    # Cast evidence values to the MotifDetectedV1 evidence value union.
    evidence_int_or_float: dict[str, str | int | float] = {
        k: v for k, v in match.evidence.items()
    }
    return MotifDetectedV1(
        event_id=f"mot_{uuid4().hex[:24]}",
        event_ts_ms=now_ms,
        ingest_ts_ms=now_ms,
        source="brain-graph",
        tenant_id=tenant_id,
        motif=match.motif,  # type: ignore[arg-type]
        members=members,
        confidence=float(match.confidence),
        score=score,
        evidence=evidence_int_or_float,
    )
