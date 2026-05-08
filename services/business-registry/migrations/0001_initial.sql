-- business-registry Postgres schema. Verified business senders for the
-- scoring pipeline — looking up an MSISDN or short-code returns the
-- business profile so brain-* services can apply confidence discounts.

BEGIN;

CREATE TABLE IF NOT EXISTS businesses (
    id                  UUID PRIMARY KEY,
    name                TEXT NOT NULL,
    registration_number TEXT,
    -- 'pending' | 'verified' | 'suspended' | 'revoked'
    status              TEXT NOT NULL DEFAULT 'pending',
    verified_at         TIMESTAMPTZ,
    verified_by         UUID,
    revoked_at          TIMESTAMPTZ,
    metadata            JSONB NOT NULL DEFAULT '{}',
    tenant_id           TEXT NOT NULL DEFAULT 'mtn-ghana',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS businesses_status_idx ON businesses (status);
CREATE INDEX IF NOT EXISTS businesses_name_lower_idx ON businesses (lower(name));

CREATE TABLE IF NOT EXISTS business_msisdns (
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    msisdn          TEXT NOT NULL,
    -- 'voice' | 'sms' | 'both'
    kind            TEXT NOT NULL DEFAULT 'both',
    verified_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (business_id, msisdn)
);

CREATE UNIQUE INDEX IF NOT EXISTS business_msisdns_msisdn_unique
    ON business_msisdns (msisdn);

CREATE TABLE IF NOT EXISTS business_shortcodes (
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    shortcode       TEXT NOT NULL,
    verified_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (business_id, shortcode)
);

CREATE UNIQUE INDEX IF NOT EXISTS business_shortcodes_unique
    ON business_shortcodes (shortcode);

-- False-positive telemetry — populated by api-noc nightly job that joins
-- alerts on verified business MSISDNs/shortcodes and aggregates rates.
CREATE TABLE IF NOT EXISTS business_false_positives (
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    window_start    DATE NOT NULL,
    alerts_total    INT NOT NULL DEFAULT 0,
    alerts_fp       INT NOT NULL DEFAULT 0,
    fp_rate         NUMERIC(4,3) GENERATED ALWAYS AS (
        CASE WHEN alerts_total = 0 THEN 0
             ELSE alerts_fp::NUMERIC / alerts_total
        END
    ) STORED,
    PRIMARY KEY (business_id, window_start)
);

COMMIT;
