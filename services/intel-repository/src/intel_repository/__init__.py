"""Fraud intelligence repository.

Aggregates signals, actions, and analyst contributions into a queryable
shared store. Auto-populated from fraud.signals.v1 and actions.taken.v1.
TTL'd: entries expire after 90 days of no activity (configurable per
kind).

Kinds:
  - suspect_number          MSISDNs flagged by behavioural / content / agent-fraud
  - high_risk_destination   international ranges with elevated fraud
  - unallocated_range       ranges not assigned to any operator (spoof source)
  - scam_template           SMS template hashes from brain-content
  - spoof_indicator         CLIs that failed validation / appear in fraud contexts
  - agent_risk              composite risk scores from brain-agent-fraud

Hot lookup path (suspect_number / spoof_indicator) is Redis-cached for
sub-millisecond reads — brain-behavioural / brain-content can call
the repo during scoring without a Postgres round-trip.
"""
