-- aml-watchlist — sanctions/PEP/internal watchlist storage.
--
-- One row per watchlist entry. Source tracks provenance; refresh_id
-- groups rows from a single import run so we can prune-and-replace.

CREATE TABLE IF NOT EXISTS watchlist_entries (
  id                  UUID PRIMARY KEY,
  source              TEXT NOT NULL,            -- 'un' | 'ofac' | 'gfic' | 'internal'
  refresh_id          TEXT NOT NULL,            -- groups entries from one import
  external_id         TEXT,                     -- source-side id (UN ref, OFAC SDN id)
  category            TEXT NOT NULL,            -- 'sanctions' | 'pep' | 'criminal' | 'internal'
  name                TEXT NOT NULL,
  aliases             TEXT[] NOT NULL DEFAULT '{}',
  date_of_birth       DATE,
  country             TEXT,
  msisdns             TEXT[] NOT NULL DEFAULT '{}',  -- linked phone numbers
  national_ids        TEXT[] NOT NULL DEFAULT '{}',  -- linked Ghana cards / passport numbers
  metadata            JSONB NOT NULL DEFAULT '{}',
  active              BOOLEAN NOT NULL DEFAULT TRUE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS watchlist_entries_source_idx
  ON watchlist_entries (source) WHERE active;
CREATE INDEX IF NOT EXISTS watchlist_entries_refresh_idx
  ON watchlist_entries (refresh_id);
CREATE INDEX IF NOT EXISTS watchlist_entries_msisdns_idx
  ON watchlist_entries USING gin (msisdns);
CREATE INDEX IF NOT EXISTS watchlist_entries_national_ids_idx
  ON watchlist_entries USING gin (national_ids);


-- Source metadata: last refresh status, row counts, error.
CREATE TABLE IF NOT EXISTS watchlist_sources (
  source              TEXT PRIMARY KEY,
  last_refresh_at     TIMESTAMPTZ,
  last_refresh_id     TEXT,
  last_refresh_status TEXT,                     -- 'success' | 'failed'
  last_error          TEXT,
  entry_count         INT NOT NULL DEFAULT 0,
  feed_url            TEXT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- Match audit: every check is logged so the ops team can review false
-- positives and tune the threshold.
CREATE TABLE IF NOT EXISTS watchlist_match_log (
  id                  UUID PRIMARY KEY,
  query_kind          TEXT NOT NULL,            -- 'name' | 'msisdn' | 'national_id'
  query_value_hash    TEXT NOT NULL,            -- never log the raw query (PII)
  matched_entry_id    UUID,
  match_score         NUMERIC(4,3),
  threshold           NUMERIC(4,3),
  outcome             TEXT NOT NULL,            -- 'hit' | 'miss'
  caller              TEXT,                     -- service name making the check
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS watchlist_match_log_outcome_idx
  ON watchlist_match_log (outcome, created_at DESC);
