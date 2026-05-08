-- api-enterprise — Phase 4 multi-tenant B2B portal schema.
--
-- Each B2B customer is a tenant in the `enterprise_tenants` table. Tenant
-- isolation is enforced two ways:
--   1. Every query carries `tenant_slug` and joins through `tenant_subscribers`.
--   2. RLS policies on `tenant_subscribers`, `shared_flags`, and
--      `enterprise_block_requests` key on `current_setting('fraudnet.tenant_id')`,
--      set per-connection in `db.Database.acquire(tenant_id=...)`.
--
-- The `enterprise_tenants` table itself is *not* under RLS — admins
-- (SYSTEM_ADMIN with step-up) need to list tenants for provisioning.

CREATE TABLE IF NOT EXISTS enterprise_tenants (
  id                          UUID PRIMARY KEY,
  slug                        TEXT NOT NULL UNIQUE,
  name                        TEXT NOT NULL,
  status                      TEXT NOT NULL DEFAULT 'active',  -- active | suspended | offboarded
  federation_enabled          BOOLEAN NOT NULL DEFAULT FALSE,
  rate_limit_capacity         INT NOT NULL DEFAULT 60,
  rate_limit_refill_per_s     NUMERIC(8,2) NOT NULL DEFAULT 10.00,
  contact_email               TEXT,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS enterprise_tenants_status_idx
  ON enterprise_tenants (status)
  WHERE status = 'active';


-- Subscribers are the unit of B2B isolation: a tenant only sees alerts whose
-- subject is one of its declared subscribers. Phase 4 provisioning ingests
-- subscriber lists per tenant; in production the list is updated by a
-- nightly sync from the tenant's BSS.
CREATE TABLE IF NOT EXISTS tenant_subscribers (
  tenant_slug         TEXT NOT NULL REFERENCES enterprise_tenants(slug)
                              ON DELETE CASCADE,
  subscriber_kind     TEXT NOT NULL,        -- 'number' | 'wallet' | 'device'
  subscriber_id       TEXT NOT NULL,        -- msisdn | wallet_id | imei
  added_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_slug, subscriber_kind, subscriber_id)
);

CREATE INDEX IF NOT EXISTS tenant_subscribers_subject_idx
  ON tenant_subscribers (subscriber_kind, subscriber_id);

ALTER TABLE tenant_subscribers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_subscribers_isolation ON tenant_subscribers;
CREATE POLICY tenant_subscribers_isolation ON tenant_subscribers
  USING (
    tenant_slug = current_setting('fraudnet.tenant_id', true)
    OR current_setting('fraudnet.tenant_id', true) IS NULL
    OR current_setting('fraudnet.tenant_id', true) = ''
  );


-- shared_flags — fraud intelligence shared between tenants via federation.
-- PII never crosses; identifier_hash is a salted SHA-256 (see federation
-- package). evidence is structured + size-bounded.
CREATE TABLE IF NOT EXISTS shared_flags (
  id                  UUID PRIMARY KEY,
  sender_tenant       TEXT NOT NULL REFERENCES enterprise_tenants(slug),
  recipient_tenant    TEXT NOT NULL REFERENCES enterprise_tenants(slug),
  identifier_kind     TEXT NOT NULL,                -- 'msisdn' | 'wallet' | 'imei'
  identifier_hash     TEXT NOT NULL,                -- salted hash; never plaintext
  indicator_kind      TEXT NOT NULL,                -- 'mule' | 'smishing' | 'voice_scam' | ...
  confidence          NUMERIC(4,3) NOT NULL,
  evidence            JSONB NOT NULL DEFAULT '{}',
  shared_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at          TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS shared_flags_recipient_idx
  ON shared_flags (recipient_tenant, shared_at DESC);
CREATE INDEX IF NOT EXISTS shared_flags_sender_idx
  ON shared_flags (sender_tenant, shared_at DESC);
CREATE INDEX IF NOT EXISTS shared_flags_hash_idx
  ON shared_flags (identifier_hash);

ALTER TABLE shared_flags ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS shared_flags_isolation ON shared_flags;
CREATE POLICY shared_flags_isolation ON shared_flags
  USING (
    sender_tenant = current_setting('fraudnet.tenant_id', true)
    OR recipient_tenant = current_setting('fraudnet.tenant_id', true)
    OR current_setting('fraudnet.tenant_id', true) IS NULL
    OR current_setting('fraudnet.tenant_id', true) = ''
  );


-- enterprise_block_requests — tenants request a cross-network block; the NOC
-- reviews and either escalates to action-tier1 or rejects.
CREATE TABLE IF NOT EXISTS enterprise_block_requests (
  id                  UUID PRIMARY KEY,
  tenant_slug         TEXT NOT NULL REFERENCES enterprise_tenants(slug),
  target_kind         TEXT NOT NULL,                 -- 'msisdn' | 'wallet' | 'url' | 'imei'
  target_value        TEXT NOT NULL,
  reason              TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'pending_review',  -- pending_review | approved | rejected | executed
  requested_by        UUID NOT NULL,                 -- Keycloak sub → uuid
  requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at          TIMESTAMPTZ,
  decision_notes      TEXT
);

CREATE INDEX IF NOT EXISTS enterprise_block_requests_tenant_idx
  ON enterprise_block_requests (tenant_slug, requested_at DESC);

ALTER TABLE enterprise_block_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS enterprise_block_requests_isolation
  ON enterprise_block_requests;
CREATE POLICY enterprise_block_requests_isolation ON enterprise_block_requests
  USING (
    tenant_slug = current_setting('fraudnet.tenant_id', true)
    OR current_setting('fraudnet.tenant_id', true) IS NULL
    OR current_setting('fraudnet.tenant_id', true) = ''
  );
