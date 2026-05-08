// FraudNet 2.0 graph schema (CLAUDE.md §6.2).
// Applied at deploy time by the graph-client bootstrap; idempotent.

// --- Indexes (MANDATORY) ---
CREATE INDEX ON :Number(msisdn);
CREATE INDEX ON :Wallet(wallet_id);
CREATE INDEX ON :Device(imei);
CREATE INDEX ON :Account(account_hash);
CREATE INDEX ON :Ring(ring_id);

// Tenant-scoped composite indexes (Phase 4)
CREATE INDEX ON :Number(tenant_id, msisdn);
CREATE INDEX ON :Wallet(tenant_id, wallet_id);

// --- Constraints ---
// Memgraph syntax differs from Neo4j; uses CONSTRAINT ON ... ASSERT IS UNIQUE
CREATE CONSTRAINT ON (n:Number) ASSERT n.msisdn IS UNIQUE;
CREATE CONSTRAINT ON (w:Wallet) ASSERT w.wallet_id IS UNIQUE;
CREATE CONSTRAINT ON (d:Device) ASSERT d.imei IS UNIQUE;
CREATE CONSTRAINT ON (r:Ring) ASSERT r.ring_id IS UNIQUE;
