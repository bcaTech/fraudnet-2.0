"""brain-content — SMS content classification.

Two paths per CLAUDE.md §5.3:
  - Fast: URL reputation lookup + known-bad template_hash check (<1 ms p99)
  - Model: keyword/pattern classifier (Phase 1 heuristic; Phase 2 replaces
    with a fine-tuned sentence-transformer + small classifier).

Body access: brain-content reads the SMS body only when ingest-sms captured
it (purpose-gated upstream). When body is None, classification falls back
to body_hash and template_hash signals.
"""

__version__ = "0.1.0"
