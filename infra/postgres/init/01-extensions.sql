-- Extensions for the main fraudnet database.
\connect fraudnet

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- digest(), gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- text search on alerts
CREATE EXTENSION IF NOT EXISTS btree_gin;

\connect fraudnet_audit

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
