"""Prompt construction for the investigation agent.

Two responsibilities:
  1. PII-redact the evidence package before it leaves this service.
  2. Render evidence into a structured prompt the model can reason over
     without hallucinating new facts.

The system prompt enforces:
  - "Only reference data present in the evidence section."
  - "Always state confidence."
  - "Always list what data was unavailable."

The output schema is JSON; the agent enforces the shape via the
`InvestigationReport` Pydantic model on parse.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


# Match an E.164 MSISDN; the prompt redacts these to a stable token
# (keeps the suffix-3 so analysts can correlate a token with the alert).
_MSISDN_RE = re.compile(r"\+?\d{8,15}")
# Wallet IDs in the FraudNet schema are alphanumeric strings ≥ 8 chars.
# Less specific; we redact only when explicitly tagged in evidence.


def _stable_token(value: str, *, prefix: str) -> str:
    """Return a deterministic token for a value.

    `prefix` distinguishes kinds (NUM, WAL, DEV, ACC) so the analyst can
    skim the report and tell which redacted token referred to a number
    vs a wallet.
    """
    h = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{prefix}_{h}"


def redact_msisdn(msisdn: str) -> str:
    return _stable_token(msisdn, prefix="NUM")


def redact_wallet(wallet_id: str) -> str:
    return _stable_token(wallet_id, prefix="WAL")


def redact_imei(imei: str) -> str:
    return _stable_token(imei, prefix="DEV")


def redact_account(account_hash: str) -> str:
    return _stable_token(account_hash, prefix="ACC")


def redact_for_prompt(text: str) -> str:
    """Best-effort scrubber for free-text fields.

    Replaces anything that looks like an MSISDN with a stable token.
    Other identifiers (wallet ID, IMEI) get redacted at the structured
    level — there is no robust regex for them.
    """
    return _MSISDN_RE.sub(lambda m: redact_msisdn(m.group()), text)


# ---------------------------------------------------------------------------
# Evidence package
# ---------------------------------------------------------------------------


@dataclass
class EvidencePackage:
    """Structured evidence for the investigator agent.

    Every field is optional. Missing data is *signalled* via
    `not_available` so the agent can comment on what was missing rather
    than hallucinating.
    """

    target_kind: str       # 'alert' | 'ring' | 'number' | 'wallet' | 'device'
    target_id: str         # plaintext id of the investigation target
    redacted_target: str

    alert: dict[str, Any] | None = None
    ring: dict[str, Any] | None = None
    ring_members: list[dict[str, Any]] = field(default_factory=list)

    feature_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    subgraph_summary: dict[str, Any] | None = None
    subgraph_nodes: list[dict[str, Any]] = field(default_factory=list)
    subgraph_edges: list[dict[str, Any]] = field(default_factory=list)
    prior_alerts: list[dict[str, Any]] = field(default_factory=list)
    prior_decisions: list[dict[str, Any]] = field(default_factory=list)
    motif_matches: list[dict[str, Any]] = field(default_factory=list)
    watchlist_hits: list[dict[str, Any]] = field(default_factory=list)

    not_available: list[str] = field(default_factory=list)


def render_user_prompt(evidence: EvidencePackage) -> str:
    """Render the evidence package as a model-readable user message.

    JSON-formatted so the model can parse without inferring structure.
    Empty sections are still included so the model knows what was
    *deliberately* empty vs missing.
    """
    payload = {
        "target": {
            "kind": evidence.target_kind,
            "redacted_id": evidence.redacted_target,
        },
        "alert": evidence.alert,
        "ring": evidence.ring,
        "ring_members": evidence.ring_members,
        "feature_snapshots": evidence.feature_snapshots,
        "subgraph_summary": evidence.subgraph_summary,
        "subgraph_nodes": evidence.subgraph_nodes,
        "subgraph_edges": evidence.subgraph_edges,
        "prior_alerts": evidence.prior_alerts,
        "prior_decisions": evidence.prior_decisions,
        "motif_matches": evidence.motif_matches,
        "watchlist_hits": evidence.watchlist_hits,
        "not_available": evidence.not_available,
    }
    body = json.dumps(payload, indent=2, sort_keys=True, default=str)
    return (
        "Investigation request. Analyse the evidence package below and "
        "produce an investigation report in the exact JSON schema "
        "specified in the system prompt.\n\n"
        f"<evidence>\n{body}\n</evidence>\n\n"
        "Reply with ONLY the JSON object — no commentary outside it."
    )


SYSTEM_PROMPT = """\
You are FraudNet 2.0's investigation assistant. You support a human fraud
analyst at MTN Ghana. You DO NOT make decisions; you produce a report
that the analyst will review and then approve or reject.

HARD RULES — non-negotiable:
1. Only reference data that appears in the <evidence> block of the user
   message. Never invent MSISDNs, wallet IDs, ring members, motif names,
   timestamps, or any other fact. If a fact is not in the evidence, you
   must list it under `data_gaps` instead.
2. Identifiers in the evidence are redacted (NUM_*, WAL_*, DEV_*, ACC_*).
   Refer to them by their redacted token. Do NOT attempt to "decode" or
   guess plaintext.
3. Always state your `confidence` as one of: low | medium | high. Justify
   the level under `confidence_rationale`.
4. Always populate `data_gaps` — list every category of evidence that
   was not available (empty or null in the evidence block) and that
   would have changed your analysis.
5. Do not recommend Tier 1 (inline) actions. Tier 1 actions are reserved
   for the decisions service's automated policy. You may recommend Tier
   2 (customer alert / friction prompt) and Tier 3 (NOC investigation /
   takedown) actions.
6. If the evidence is insufficient for a meaningful conclusion, set
   `confidence=low` and `recommended_actions=[]`. Do not invent a
   conclusion just to fill the schema.

OUTPUT SCHEMA — return exactly this JSON shape, no extras:

{
  "summary": "<2-3 sentence executive summary>",
  "risk_assessment": "<paragraph: what the evidence suggests>",
  "key_findings": ["<bullet>", "<bullet>", ...],
  "evidence_chain": [
    {"observation": "<what>", "source": "<which evidence field>"}
  ],
  "recommended_actions": [
    {"tier": "tier2"|"tier3", "action": "<short>", "rationale": "<why>"}
  ],
  "data_gaps": ["<category that was missing>", ...],
  "confidence": "low"|"medium"|"high",
  "confidence_rationale": "<one paragraph explaining the level>"
}
"""
