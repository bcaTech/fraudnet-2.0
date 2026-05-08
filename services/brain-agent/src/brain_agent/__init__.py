"""brain-agent — GenAI investigation assistant.

Best-of-breed sprint deliverable. The agent does not score; it composes a
structured evidence package (subgraph + features + alert history + prior
decisions), submits it to Claude (Anthropic API), and returns a
human-readable investigation report for analyst review.

Hard rules:
  - The agent MUST NOT make decisions. It produces a report; the analyst
    approves or rejects via api-noc.
  - The system prompt forbids hallucination. The model must only
    reference data present in the supplied context, must always state a
    confidence level, and must list what data was unavailable.
  - PII redaction lives at the prompt-construction boundary
    (`prompt.py.redact_for_prompt`); raw plaintext MSISDNs and wallet IDs
    do not leave this service.
  - Per-analyst rate limit (10/hour) is enforced by Redis token bucket
    to control LLM cost. Group-admin role bypasses for incident triage.
"""
