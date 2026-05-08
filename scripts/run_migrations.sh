#!/usr/bin/env bash
# Apply Postgres migrations for api-noc and compliance.
#
# api-noc        → fraudnet           (alerts, rings, takedowns, users)
# compliance     → fraudnet_audit     (append-only audit log, WORM)
#
# Migrations are plain SQL files under services/<svc>/migrations, applied in
# lexicographic order (0001_… → 0002_… → …). State is tracked in a
# schema_migrations table per database; already-applied files are skipped.
#
# Idempotent. Safe to re-run on every container start.

set -euo pipefail

POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-fraudnet}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-fraudnet_dev}"
POSTGRES_DB="${POSTGRES_DB:-fraudnet}"
AUDIT_DB="${AUDIT_DB:-fraudnet_audit}"
WAIT_SECS="${PG_WAIT_SECS:-60}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PGPASSWORD="$POSTGRES_PASSWORD"

# psql may not exist in the dev image — install on demand.
if ! command -v psql >/dev/null 2>&1; then
  echo "psql not on PATH; installing postgresql-client…"
  apt-get update >/dev/null && apt-get install -y --no-install-recommends postgresql-client >/dev/null
fi

# ---- wait for Postgres -----------------------------------------------------
echo "==> Waiting for Postgres at $POSTGRES_HOST:$POSTGRES_PORT (≤${WAIT_SECS}s)"
deadline=$(( $(date +%s) + WAIT_SECS ))
until psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -c 'SELECT 1' >/dev/null 2>&1; do
  if [[ $(date +%s) -gt $deadline ]]; then
    echo "  ✗ Postgres did not become reachable" >&2
    exit 1
  fi
  sleep 1
done
echo "  ✓ Postgres reachable"

# ---- ensure databases exist ------------------------------------------------
ensure_db() {
  local dbname="$1"
  if ! psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname='$dbname'" | grep -q 1; then
    echo "  + creating database '$dbname'"
    psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres \
      -c "CREATE DATABASE $dbname OWNER $POSTGRES_USER" >/dev/null
  else
    echo "  = database '$dbname' exists"
  fi
}

echo "==> Ensuring databases"
ensure_db "$POSTGRES_DB"
ensure_db "$AUDIT_DB"

# ---- migration runner ------------------------------------------------------
apply_migrations() {
  local svc="$1"
  local target_db="$2"
  local migdir="$ROOT/services/$svc/migrations"

  if [[ ! -d "$migdir" ]]; then
    echo "  ! $svc has no migrations dir, skipping"
    return 0
  fi

  echo "==> Applying $svc migrations → $target_db"

  # Bootstrap the migrations bookkeeping table.
  psql -v ON_ERROR_STOP=1 \
       -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$target_db" <<'SQL' >/dev/null
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  service TEXT NOT NULL
);
SQL

  applied_count=0
  skipped_count=0
  for f in "$migdir"/*.sql; do
    [[ -f "$f" ]] || continue
    base="$(basename "$f")"

    already=$(psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$target_db" \
              -tAc "SELECT 1 FROM schema_migrations WHERE filename='$base' AND service='$svc'" || echo "")
    if [[ "$already" == "1" ]]; then
      echo "  = $base (applied)"
      skipped_count=$((skipped_count + 1))
      continue
    fi

    echo "  + $base"
    psql -v ON_ERROR_STOP=1 \
         -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$target_db" \
         -f "$f" >/dev/null

    psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$target_db" \
         -c "INSERT INTO schema_migrations(filename, service) VALUES ('$base', '$svc')" >/dev/null
    applied_count=$((applied_count + 1))
  done

  echo "  ✓ $svc: applied=$applied_count skipped=$skipped_count"
}

apply_migrations api-noc    "$POSTGRES_DB"
apply_migrations compliance "$AUDIT_DB"

echo "OK: migrations complete"
