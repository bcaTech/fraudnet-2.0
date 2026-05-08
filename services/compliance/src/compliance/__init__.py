"""compliance — audit consumer + WORM storage + regulator export.

Per CLAUDE.md §7.3, the audit log is the single source of truth for
regulator inquiries. This service:

  - Consumes `audit.events.v1` and persists to the fraudnet_audit Postgres
    database (WORM-style: append-only, monthly partitions, archived to
    Iceberg after 6 months — Phase 2 wires the Iceberg rotation cron).
  - Consumes `decisions.dispatched.v1` and persists to `decision_audits`
    so every dispatched decision is traceable back to the firing policy.
  - Exposes a thin REST surface for regulator export by date range and
    audit lookup by request_id / actor_id (read-only; no writes from the
    API).

Phase 2 scope (not in this release):
  - Purpose-limitation enforcer sidecar.
  - Iceberg archive cron.
  - Regulator submission templates per regulator (NCA / DPC / BoG / CSA).
"""

__version__ = "0.1.0"
