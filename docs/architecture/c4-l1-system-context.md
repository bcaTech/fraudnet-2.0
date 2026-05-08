# C4 Level 1 — System context

Where FraudNet 2.0 sits in MTN Ghana's operational environment. Each
arrow is a real integration; the boxes outside FraudNet are systems we
do not own.

```mermaid
C4Context
title FraudNet 2.0 — System Context (Phase 4)

Person(noc, "NOC investigator", "Operates the workbench, runs takedowns")
Person(customer, "MTN customer", "Subscriber on Ghana network")
Person(b2b, "Enterprise customer", "B2B tenant: bank / large corporate")
Person(group, "MTN Group analyst", "Cross-opco fraud strategy")

System(fraudnet, "FraudNet 2.0", "Network-native fraud intelligence platform")

System_Ext(probe, "Network probes", "Polystar / NetScout / Subex — SS7 / Diameter / IMS")
System_Ext(smsc, "SMSC", "MTN messaging core")
System_Ext(dns, "DNS resolver / IPDR feed", "Network telemetry")
System_Ext(momo, "MoMo BSS", "Mobile money platform")
System_Ext(ims, "IMS / VoLTE core", "Inline call signaling")
System_Ext(keycloak, "Keycloak", "Identity provider for staff + B2B")
System_Ext(vault, "Vault", "Secrets management")
System_Ext(regulator, "Regulators", "NCA, DPC, BoG, CSA, GSMA T-ISAC")
System_Ext(peer_opcos, "Peer MTN opcos", "Federation peers — Uganda, Cameroon, Côte d'Ivoire ...")

Rel(probe, fraudnet, "Voice signaling events")
Rel(smsc, fraudnet, "SMS metadata + content")
Rel(dns, fraudnet, "DNS / IPDR")
Rel(momo, fraudnet, "MoMo events")
Rel(fraudnet, ims, "VoLTE handset tag (Tier 1)")
Rel(fraudnet, dns, "DNS sinkhole push (Tier 1)")
Rel(fraudnet, momo, "Send-with-Care prompt (Tier 1)")

Rel(noc, fraudnet, "Workbench / takedown")
Rel(customer, fraudnet, "Self-service: alerts, report, block")
Rel(b2b, fraudnet, "B2B portal")
Rel(group, fraudnet, "Group view (Phase 4)")

Rel(fraudnet, keycloak, "Auth")
Rel(fraudnet, vault, "Secrets")
Rel(fraudnet, regulator, "Submission packs")
Rel_Bi(fraudnet, peer_opcos, "Federation: hashed flags only")
```

**What to look for.** FraudNet has hard inputs (probes, SMSC, MoMo, DNS),
hard outputs (IMS, DNS sinkhole, MoMo BSS), and four classes of human
user. Phase 4's defining addition is the bidirectional link to peer
opcos: cross-opco fraud intelligence flows, but only as hashed
identifiers — see `federation-protocol.md`.
