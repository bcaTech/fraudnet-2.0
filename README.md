# FraudNet 2.0 — Backend

MTN Ghana's network-native, AI-driven fraud intelligence platform. Telco-scale ingestion of voice, SMS, MoMo, and data signals fused on a real-time fraud graph, with three latency tiers of action.

The authoritative engineering specification is [`CLAUDE.md`](./CLAUDE.md). Read it before contributing.

## Quickstart

```bash
make bootstrap          # Python venv, pre-commit, workspace install
make infra-up           # Kafka, Postgres, Memgraph, Aerospike, MinIO, etc.
make seed               # Sample data
make dev                # All services (or: make dev SERVICE=ingest-momo)
```

## Repository layout

| Path | Purpose |
|---|---|
| `services/ingest-*` | Probe / SMSC / MoMo / DNS / intel adapters → Kafka |
| `services/stream-*` | Flink jobs: feature computation, real-time graph mutation |
| `services/brain-*` | Behavioural / content / graph model serving |
| `services/decisions` | Tier dispatcher with YAML-driven policy |
| `services/action-tier{1,2,3}` | Inline / NRT / investigation actuators |
| `services/api-*` | NOC, customer, enterprise, admin, public gateway |
| `services/compliance` | Audit, purpose limitation, regulator export |
| `services/feedback` | Label ingest + retraining triggers |
| `packages/` | Shared libraries (schemas, clients, obs, auth, audit, testing) |
| `infra/` | Kustomize, Terraform, Kafka topic definitions, Flink jobs |
| `docs/` | Runbooks, ADRs, data contracts |
| `tools/` | Load generator, replay tooling, data-quality checks |

## Development

- Python 3.12 + FastAPI for service layer; PyTorch / LightGBM / sentence-transformers for models; Apache Flink for stream processing.
- `uv` for workspace + dependency management; `turbo` for build orchestration.
- `ruff` (lint + format), `mypy --strict` (typing), `pytest` (test).
- One PR = one logical change. Conventional Commits. ADR for architectural shifts.

## Contacts

Programme lead, security lead, DPO liaison, and on-call rotation are documented per service in `docs/runbooks/{service}.md`.
