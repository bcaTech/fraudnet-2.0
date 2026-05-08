# FraudNet 2.0 — architecture diagrams

C4 model + supporting flow diagrams. Source-of-truth pictures of the
system; update when contracts change. Prefer adding a new diagram over
editing an old one when the change is large.

| Diagram | Level | Purpose |
| --- | --- | --- |
| [`c4-l1-system-context.md`](./c4-l1-system-context.md) | C4 L1 | Where FraudNet sits in MTN's environment |
| [`c4-l2-containers.md`](./c4-l2-containers.md) | C4 L2 | Every deployable service + its data store |
| [`federation-protocol.md`](./federation-protocol.md) | sequence | Cross-opco federation handshake (Phase 4) |
| [`data-flow.md`](./data-flow.md) | flow | End-to-end probe → action |
| [`tenant-isolation.md`](./tenant-isolation.md) | layer | How tenant boundaries are enforced |

All diagrams are Mermaid; render via the GitHub UI or any Mermaid-aware
viewer (VS Code extension, mermaid-cli).

## Conventions

- C4 levels follow Simon Brown's spec. Don't mix levels in one diagram —
  zoom into a separate file instead.
- Service names match the directory names under `services/` exactly.
- External systems (Keycloak, Vault, peer opcos) live on the boundary
  with a distinct shape.
- Every shipped diagram has a one-paragraph caption that tells you what
  to look for.
