# End-to-end data flow

Probe → action. The hot path that has to come in under the 200 ms
inline budget for VoLTE handset tagging.

```mermaid
flowchart TB
    P[Network probe<br/>SS7/Diameter event] -->|≤30ms| IV[ingest-voice]
    IV -->|voice.events.v1| K1[(Kafka)]
    K1 --> SF[stream-features<br/>Flink]
    K1 --> SG[stream-graph<br/>Flink]

    SF -->|windowed features| AERO[(Aerospike<br/>1ms p99 reads)]
    SF -->|append| ICE[(Iceberg)]
    SG -->|MERGE node/edge| MG[(Memgraph)]
    SG -->|graph.mutations.v1| K2[(Kafka)]

    AERO --> BB[brain-behavioural<br/>5ms p99]
    K2 --> BG[brain-graph<br/>motif + GNN]
    BG -->|motifs.detected.v1| DEC[decisions]
    BB --> DEC

    DEC -->|policy YAML routing| K3[(decisions.dispatched.v1)]
    K3 --> A1[action-tier1]
    A1 -->|SIP header rewrite| IMS[(IMS / VoLTE)]
    A1 -->|sinkhole push| DNSink[(DNS sinkhole)]
    A1 -->|Send-with-Care prompt| MoMo[(MoMo BSS)]

    K3 --> A2[action-tier2<br/>customer alerts]
    K3 --> A3[action-tier3<br/>NOC investigation queue]

    %% Cross-cutting
    A1 -.->|actions.taken.v1| FB[feedback]
    A2 -.->|actions.taken.v1| FB
    A3 -.->|actions.taken.v1| FB
    FB --> BB
    FB --> BG

    %% Federation seam (Phase 4)
    BG <-.->|hashed lookup| FED[federation peers]:::p4

    classDef p4 fill:#ffe9b3,stroke:#a86a00,color:#000;
```

**Latency budget by hop.**

| Hop | Budget (p99) | Why |
| --- | --- | --- |
| Probe → Kafka | 30 ms | Vendor-side path |
| Kafka → Aerospike read | 5 ms | In-memory feature store |
| brain-behavioural score | 5 ms | LightGBM + tiny seq model |
| brain-graph motif (cached) | 30 ms | GNN inference is heavier; cached aggressively |
| decisions policy | 5 ms | YAML lookup + dedupe |
| action-tier1 dispatch | 50 ms | RPC to IMS / DNS / MoMo |
| Total | < 200 ms | VoLTE inline budget |

Cross-opco federation lookup is **not** on this path — it runs only in
the scheduled batch in brain-graph (every 15 min) where the latency
budget is generous (peer round-trip can be hundreds of ms).
