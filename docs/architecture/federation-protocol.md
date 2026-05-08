# Federation protocol — sequence

How brain-graph + api-enterprise talk to a peer opco's federation
endpoint to detect cross-opco rings without leaking PII.

```mermaid
sequenceDiagram
    autonumber
    participant BG as brain-graph (Ghana)
    participant FED as packages/federation client
    participant Net as TLS / HMAC
    participant SRV as Federation server (Uganda)
    participant MG as Memgraph (Uganda)
    participant DEC as decisions (Ghana)

    Note over BG: Local batch found a ring with outgoing wallet flow
    BG->>FED: detect_cross_opco_rings(rings, subgraph)

    Note over FED: Hash every external identifier locally
    FED->>FED: hash_identifier(msisdn, kind="msisdn", salt=v1)

    loop one bulk call per peer
        FED->>Net: POST /federation/v1/flags/lookup<br/>X-Federation-Peer: opco-ghana<br/>X-Federation-Timestamp + X-Federation-Signature
        Net->>SRV: HMAC-verified request<br/>(salted SHA-256 hashes only)
        SRV->>SRV: verify_signature(secret_for_peer)
        SRV->>MG: lookup_flags(hashes)
        MG-->>SRV: matched flags
        SRV-->>Net: FederationLookupResponse
        Net-->>FED: hashed flags + salt_version
    end

    Note over FED: Composite score lifted by peer confirmations
    FED-->>BG: list[CrossOpcoRing]
    BG->>BG: emit MotifDetectedV1(motif="cross_opco_ring")
    BG->>DEC: motifs.detected.v1 (Kafka)

    Note over DEC: Decisions dispatches Tier 1/2/3 actions<br/>per cross-opco policy
```

**What to look for.**

1. **Hash before send.** Step 3 happens in this opco's process. The
   wire never carries plaintext — the network layer (step 4) only ever
   sees the hex digest. This is the architectural enforcement of
   CLAUDE.md §7.5.
2. **HMAC + freshness window.** Step 5 verifies the signature against
   the peer-pair shared secret (Phase 4) or the SPIFFE workload identity
   (Phase 4.5). Stale or unsigned requests are dropped before the
   adapter is called.
3. **Hashed-only response.** Step 7 never decodes a hash back to
   plaintext — the peer Memgraph adapter hashes inside the Cypher
   RETURN clause so plaintext does not leak even via a server-side bug.
4. **Decisions sees a normal motif.** The downstream pipeline does not
   need to know that this motif came from federation — `decisions`
   already handles the full motif catalogue. Cross-opco prioritisation
   is via the policy YAML (`services/decisions/policies/`).

## Failure semantics

| Failure | Effect |
| --- | --- |
| Network timeout | Single peer skipped (logged, metric incremented); other peers still queried |
| HMAC verification fails | 401 to the peer; `federation_server_requests_total{outcome=auth_invalid}` |
| Stale timestamp | 401; replay window is 5 min |
| Salt rotation in flight | Both `v1` and `v2` accepted for 7 days |
| Unknown peer | 401 with `outcome=auth_no_peer`; never returns "yes I have data on X" |
