-- Bootstrap databases for the local stack.
-- Production schemas live as versioned migrations under services/*/migrations/.

-- Keycloak's own database
CREATE DATABASE keycloak;
CREATE USER keycloak WITH PASSWORD 'keycloak_dev';
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;

-- Audit log lives on its own logical database (CLAUDE.md §5.5 — its own
-- Postgres instance with WORM retention in production).
CREATE DATABASE fraudnet_audit;
GRANT ALL PRIVILEGES ON DATABASE fraudnet_audit TO fraudnet;

-- Enterprise tenant database for Phase 4 — stub for now.
CREATE DATABASE fraudnet_enterprise;
GRANT ALL PRIVILEGES ON DATABASE fraudnet_enterprise TO fraudnet;
