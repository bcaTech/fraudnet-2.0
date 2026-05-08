"""brain-behavioural — behavioural scoring service.

Phase 1 ships a heuristic scoring model behind a fixed `Scorer` interface
(DECISIONS.md D-006). Phase 2 replaces the model artefact via the model
registry without API changes.

Two paths:
  - Async: consume voice/sms/momo events, score the subject's current
    feature snapshot from Aerospike, emit SignalEventV1 if above threshold.
  - Sync: REST `POST /score/number` and `POST /score/wallet` for ad-hoc
    scoring (used by api-noc and decisions when blocking is acceptable).
"""

__version__ = "0.1.0"
