-- intel-repository — shared fraud intelligence database.
--
-- Single `intel_entries` table (entries are tagged by `kind`) plus a
-- per-kind risk-roll-up view. The fraud signals/actions feeders write
-- here; brain-* services read here as an enrichment source during
-- scoring.

CREATE TABLE IF NOT EXISTS intel_entries (
  id              UUID PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT 'mtn-ghana',
  kind            TEXT NOT NULL,
  -- 'suspect_number'
  -- 'high_risk_destination'
  -- 'unallocated_range'
  -- 'scam_template'
  -- 'spoof_indicator'
  -- 'agent_risk'
  identifier      TEXT NOT NULL,
  -- shape of identifier varies by kind (msisdn, cli, range_prefix, hash, ...)
  metadata        JSONB NOT NULL DEFAULT '{}',
  risk_score      NUMERIC(4,3) NOT NULL DEFAULT 0,
  hit_count       INT NOT NULL DEFAULT 0,
  first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ NOT NULL,
  contributor     TEXT NOT NULL,             -- service or analyst id
  active          BOOLEAN NOT NULL DEFAULT TRUE,
  CONSTRAINT intel_entries_unique UNIQUE (tenant_id, kind, identifier)
);

CREATE INDEX IF NOT EXISTS intel_entries_kind_score_idx
  ON intel_entries (kind, risk_score DESC) WHERE active;
CREATE INDEX IF NOT EXISTS intel_entries_expires_idx
  ON intel_entries (expires_at) WHERE active;
CREATE INDEX IF NOT EXISTS intel_entries_last_seen_idx
  ON intel_entries (last_seen_at DESC) WHERE active;
CREATE INDEX IF NOT EXISTS intel_entries_metadata_idx
  ON intel_entries USING gin (metadata);


-- View: per-kind counts + average risk + max age. Used by /intel/stats.
CREATE OR REPLACE VIEW intel_stats AS
SELECT
    kind,
    count(*)                         AS active_count,
    round(avg(risk_score)::numeric, 3) AS avg_risk_score,
    max(last_seen_at)                AS most_recent_at,
    min(first_seen_at)               AS oldest_at
  FROM intel_entries
 WHERE active
 GROUP BY kind;
