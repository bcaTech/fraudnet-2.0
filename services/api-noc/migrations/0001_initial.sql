-- api-noc Postgres schema. Authoritative DDL for the investigator workbench.
-- Mirrors CLAUDE.md §6.1.

BEGIN;

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY,
    sub             TEXT UNIQUE NOT NULL,           -- Keycloak subject
    email           TEXT,
    display_name    TEXT,
    role            TEXT NOT NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'mtn-ghana',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rings (
    id              UUID PRIMARY KEY,
    type            TEXT NOT NULL,                  -- voice_scam | smishing | mule | mixed
    status          TEXT NOT NULL DEFAULT 'monitoring',
    composite_score NUMERIC(4,3),
    active_since    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity   TIMESTAMPTZ NOT NULL DEFAULT now(),
    member_count    INT NOT NULL DEFAULT 0,
    metadata        JSONB NOT NULL DEFAULT '{}',
    tenant_id       TEXT NOT NULL DEFAULT 'mtn-ghana',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rings_status_last_activity_idx
    ON rings (status, last_activity DESC);

CREATE TABLE IF NOT EXISTS ring_members (
    ring_id         UUID NOT NULL REFERENCES rings(id) ON DELETE CASCADE,
    member_kind     TEXT NOT NULL,                  -- number | wallet | device
    member_id       TEXT NOT NULL,
    role            TEXT,
    confidence      NUMERIC(4,3),
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ring_id, member_kind, member_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id              UUID PRIMARY KEY,
    type            TEXT NOT NULL,                  -- voice | sms | momo | ott
    severity        TEXT NOT NULL,                  -- critical | high | medium | low
    subject_kind    TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    score           NUMERIC(4,3) NOT NULL,
    ring_id         UUID REFERENCES rings(id),
    status          TEXT NOT NULL DEFAULT 'new',    -- new | claimed | reviewing | closed | fp
    assignee_id     UUID REFERENCES users(id),
    closed_at       TIMESTAMPTZ,
    closed_reason   TEXT,
    details         JSONB NOT NULL DEFAULT '{}',
    tenant_id       TEXT NOT NULL DEFAULT 'mtn-ghana',
    decision_id     TEXT,                            -- DecisionDispatchedV1.decision_id
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS alerts_status_severity_created_idx
    ON alerts (status, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS alerts_assignee_status_idx
    ON alerts (assignee_id, status) WHERE status IN ('claimed', 'reviewing');
CREATE INDEX IF NOT EXISTS alerts_ring_idx ON alerts (ring_id);

CREATE TABLE IF NOT EXISTS takedowns (
    id              UUID PRIMARY KEY,
    ring_id         UUID NOT NULL REFERENCES rings(id),
    status          TEXT NOT NULL DEFAULT 'drafted',  -- drafted|approved|filed|acknowledged|executed|closed
    filed_with      TEXT,
    filed_at        TIMESTAMPTZ,
    evidence_hash   TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}',
    tenant_id       TEXT NOT NULL DEFAULT 'mtn-ghana',
    created_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS takedowns_ring_idx ON takedowns (ring_id);

COMMIT;
