# brain-agent

GenAI investigation assistant. Composes a structured evidence package
(subgraph + features + alert history + prior decisions), submits it to
Claude (Anthropic API), and returns a human-readable investigation
report for analyst review.

The agent **does not make decisions**. It produces a report; the
analyst approves or rejects via api-noc.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| POST | `/investigate/alert/{alert_id}` | Investigate an alert |
| POST | `/investigate/ring/{ring_id}` | Investigate a ring |
| POST | `/investigate/entity/{kind}/{id}` | Investigate a number/wallet/device |
| GET  | `/investigate/{job_id}` | Poll for the report |

`kind` is one of `number | wallet | device`. RBAC: any `FRAUD_*` role
or `GROUP_ADMIN`. Per-analyst rate limit defaults to 10/hour;
`GROUP_ADMIN` bypasses for incident triage (audit-logged).

## PII redaction

Identifiers leaving this service are redacted to stable tokens
(`NUM_<8hex>`, `WAL_<8hex>`, `DEV_<8hex>`, `ACC_<8hex>`). The mapping
is one-way; the LLM never sees plaintext MSISDNs / wallet IDs / IMEIs.
Free-text fields are passed through `redact_for_prompt` which catches
loose MSISDN-shaped strings.

## System prompt

Enforces:
1. Only reference data in the `<evidence>` block — no hallucination.
2. Use the redacted tokens; never decode them.
3. Always state `confidence` (low | medium | high) + `confidence_rationale`.
4. Always populate `data_gaps`.
5. Tier 1 (inline) actions are forbidden in recommendations — those are
   reserved for the decisions service. Tier 2 / Tier 3 only.
6. If evidence is insufficient, return `confidence=low` and empty
   `recommended_actions`.

The output schema is enforced by `report.InvestigationReport` (Pydantic
`extra=forbid`); malformed responses become low-confidence fallback
reports rather than failing the request.

## LLM

Production: `AnthropicLLMClient` against the Anthropic API, model
`claude-opus-4-7`, with prompt caching on the system message
(`cache_control=ephemeral`).

Dev: `StubLLMClient` returns a fixed valid report. Used when
`ANTHROPIC_API_KEY` is unset.

## Settings

| Env | Default | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | _empty_ | Empty → stub LLM. Required in prod. |
| `ANTHROPIC_MODEL` | `claude-opus-4-7` | Pinned to latest Opus. |
| `BRAIN_AGENT_RL_CAPACITY` | `10` | Per-analyst bucket size. |
| `BRAIN_AGENT_RL_REFILL_PER_S` | `0.00277...` | Full refill in 1h. |

## Local dev

```bash
make dev SERVICE=brain-agent
```

## Tests

```bash
pytest services/brain-agent -v
```
