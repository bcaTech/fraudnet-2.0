# C4 Level 2 — Containers

Every deployable service in `services/` plus the data plane it talks to.
Phase 1–3 services and the Phase 4 additions (federation, group portal).

```mermaid
flowchart LR

  %% External
  subgraph EXT["External"]
    PROBE[(Network probes)]
    SMSC[(SMSC)]
    DNS[(DNS / IPDR)]
    MOMO[(MoMo BSS)]
    IMS[(IMS / VoLTE)]
    KC[(Keycloak)]
    PEERS[(Peer opcos)]
  end

  %% Layer 1 — Ingestion
  subgraph L1["Layer 1 — Ingestion"]
    IV[ingest-voice]
    IS[ingest-sms]
    ID[ingest-data]
    IM[ingest-momo]
    II[ingest-intel]
  end

  %% Kafka spine
  subgraph KFK["Kafka spine"]
    K1[[voice.events.v1]]
    K2[[sms.events.v1]]
    K3[[data.events.v1]]
    K4[[momo.events.v1]]
    K5[[intel.events.v1]]
    K6[[graph.mutations.v1]]
    K7[[motifs.detected.v1]]
    K8[[decisions.dispatched.v1]]
    K9[[actions.taken.v1]]
    K10[[audit.events.v1]]
  end

  %% Layer 2 — Stream processing
  subgraph L2["Layer 2 — Stream"]
    SF[stream-features]
    SG[stream-graph]
  end

  %% Data plane
  subgraph DP["Data plane"]
    AERO[(Aerospike — feature store)]
    PG[(Postgres — alerts/rings/audit)]
    MG[(Memgraph — fraud graph)]
    ICE[(Iceberg / S3 — lakehouse)]
    REDIS[(Redis — rate limit)]
  end

  %% Layer 3 — Brain
  subgraph L3["Layer 3 — Brain"]
    BB[brain-behavioural]
    BC[brain-content]
    BG[brain-graph]
    BO[brain-otp-guard]
    UI[url-intel]
  end

  %% Layer 4 — Decisions + actions
  subgraph L4["Layer 4 — Decisions + actions"]
    DEC[decisions]
    A1[action-tier1]
    A2[action-tier2]
    A3[action-tier3]
  end

  %% APIs
  subgraph API["APIs"]
    APUB[api-public]
    ANOC[api-noc]
    ACUS[api-customer]
    AENT[api-enterprise — Phase 4]
    AADM[api-admin]
  end

  %% Cross-cutting
  subgraph X["Cross-cutting"]
    COMP[compliance]
    FB[feedback]
    BR[business-registry]
  end

  %% Phase 4
  subgraph P4["Phase 4 federation"]
    FEDPKG{{packages/federation}}
  end

  %% External -> ingestion
  PROBE --> IV
  SMSC --> IS
  DNS --> ID
  MOMO --> IM
  PEERS <--> FEDPKG

  %% Ingestion -> Kafka
  IV --> K1
  IS --> K2
  ID --> K3
  IM --> K4
  II --> K5

  %% Stream
  K1 --> SF
  K2 --> SF
  K3 --> SF
  K4 --> SF
  K1 --> SG
  K2 --> SG
  K4 --> SG
  K5 --> SG
  SF --> AERO
  SF --> ICE
  SG --> MG
  SG --> K6

  %% Brain
  AERO --> BB
  K2 --> BC
  K3 --> UI
  K6 --> BG
  BG --> K7

  %% Decisions
  K6 --> DEC
  K7 --> DEC
  BB --> DEC
  BC --> DEC
  DEC --> K8
  K8 --> A1
  K8 --> A2
  K8 --> A3
  A1 --> IMS
  A1 --> DNS
  A1 --> MOMO
  A2 --> ACUS
  A3 --> PG

  %% APIs
  APUB --> ANOC
  APUB --> ACUS
  APUB --> AENT
  APUB --> AADM
  ANOC --> PG
  ANOC --> MG
  ACUS --> PG
  AENT --> PG
  AENT --> MG
  AENT --> REDIS
  AENT <-->|"federation: hashed only"| FEDPKG
  BG <-->|"cross-opco lookup"| FEDPKG

  %% Audit
  ANOC --> K10
  ACUS --> K10
  AENT --> K10
  AADM --> K10
  K10 --> COMP
  COMP --> PG
  COMP --> ICE

  %% Feedback
  K9 --> FB
  FB --> BB
  FB --> BC
  FB --> BG

  %% Auth
  APUB -.-> KC

  classDef phase4 fill:#ffe9b3,stroke:#a86a00,color:#000;
  class AENT,FEDPKG phase4;
```

**What to look for.** Five layers, one Kafka spine, four data stores.
The Phase 4 additions are highlighted: `api-enterprise` (the B2B portal)
and `packages/federation` (the cross-opco protocol). Notice that the
federation package is the *only* path to peer opcos — every cross-opco
flow goes through it, which is where PII redaction is enforced.
