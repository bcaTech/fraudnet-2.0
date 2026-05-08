"""decisions — fraud-signal orchestrator.

Consumes `fraud.signals.v1` and `motifs.detected.v1`. Applies a YAML-driven
policy (`policies/*.yaml`) to determine action + latency tier + suppression.
Emits `DecisionDispatchedV1` to:
  - `decisions.dispatched.v1` (audit trail; consumed by compliance)
  - `action.tier{1,2,3}.v1` (per-tier; consumed by action-tier{1,2,3})

Per CLAUDE.md §5.4 and DECISIONS.md D-003, policy is YAML so regulator-
relevant decisions are reviewable without code changes.
"""

__version__ = "0.1.0"
