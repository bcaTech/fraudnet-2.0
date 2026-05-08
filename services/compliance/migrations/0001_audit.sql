-- compliance/audit DB schema. Lives in the fraudnet_audit database
-- (separate from fraudnet) for WORM retention semantics per CLAUDE.md §5.5.

BEGIN;

-- Master audit-events table (parent for monthly partitions). Append-only
-- by convention (no UPDATE / DELETE in service code). Production deploys
-- a Postgres role with INSERT-only grants on this table for the
-- compliance-writer connection.
CREATE TABLE IF NOT EXISTS audit_events (
    id              UUID PRIMARY KEY,
    actor_id        UUID,
    actor_kind      TEXT NOT NULL,
    action          TEXT NOT NULL,
    resource_kind   TEXT NOT NULL,
    resource_id     TEXT,
    purpose         TEXT NOT NULL,
    request_id      TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'mtn-ghana',
    metadata        JSONB NOT NULL DEFAULT '{}',
    event_ts        TIMESTAMPTZ NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS audit_events_action_idx ON audit_events (action);
CREATE INDEX IF NOT EXISTS audit_events_actor_idx ON audit_events (actor_id);
CREATE INDEX IF NOT EXISTS audit_events_resource_idx ON audit_events (resource_kind, resource_id);
CREATE INDEX IF NOT EXISTS audit_events_event_ts_idx ON audit_events (event_ts DESC);

-- Bootstrap a couple of months of partitions. A scheduled job rolls forward
-- each month; archived months are exported to Iceberg (audit_archive).
CREATE TABLE IF NOT EXISTS audit_events_2026_05
    PARTITION OF audit_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS audit_events_2026_06
    PARTITION OF audit_events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS audit_events_2026_07
    PARTITION OF audit_events
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS audit_events_2026_08
    PARTITION OF audit_events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- Mirror for decision audit-trail (decisions.dispatched.v1). Same shape;
-- separate logical table because the volume profile differs and we want
-- to age-out independently.
CREATE TABLE IF NOT EXISTS decision_audits (
    decision_id     TEXT PRIMARY KEY,
    tier            TEXT NOT NULL,
    action          TEXT NOT NULL,
    subject_kind    TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    severity        TEXT NOT NULL,
    score           NUMERIC(4,3),
    policy_id       TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    suppression_key TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}',
    tenant_id       TEXT NOT NULL DEFAULT 'mtn-ghana',
    event_ts        TIMESTAMPTZ NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS decision_audits_subject_idx
    ON decision_audits (subject_kind, subject_id, event_ts DESC);
CREATE INDEX IF NOT EXISTS decision_audits_policy_idx
    ON decision_audits (policy_id, policy_version);
CREATE INDEX IF NOT EXISTS decision_audits_event_ts_idx
    ON decision_audits (event_ts DESC);

COMMIT;
