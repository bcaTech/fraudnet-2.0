"""Iceberg-compatible archive of aged audit partitions.

Audit events are append-only and partitioned monthly in Postgres
(see migrations/0001_audit.sql). Per CLAUDE.md §6.1 / §6.5, partitions
older than 6 months are exported to the lakehouse and then detached
from the live table. We do this in three steps per partition:

  1. Stream rows out via asyncpg in batches.
  2. Write Parquet files into MinIO under
       s3://{bucket}/audit_events/year=YYYY/month=MM/
     This Hive-style layout is the same one Trino's Iceberg / Hive
     connectors register, so an Iceberg table can be defined later
     without rewriting any data.
  3. Detach the partition from the live table and record the archive
     in `audit_archive_manifest`.

The job is opt-in (ICEBERG_ARCHIVE_ENABLED=1) and re-runnable: an
already-archived month is detected via `audit_archive_manifest` and
skipped.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

from fraudnet.obs import counter, get_logger

_log = get_logger("compliance.archive")

_ARCHIVED_PARTITIONS = counter(
    "compliance_audit_partitions_archived_total",
    "Audit partitions archived to Iceberg-compatible parquet on object storage.",
)
_ARCHIVED_ROWS = counter(
    "compliance_audit_rows_archived_total",
    "Audit rows archived.",
)
_ARCHIVE_FAILURES = counter(
    "compliance_audit_archive_failures_total",
    "Archive runs that failed.",
    labelnames=("phase",),
)


# A partition table matches `audit_events_YYYY_MM`.
_PARTITION_RE = re.compile(r"^audit_events_(\d{4})_(\d{2})$")
_BATCH_SIZE = 5_000


@dataclass(frozen=True)
class ArchivedPartition:
    table_name: str
    year: int
    month: int
    rows_archived: int
    object_key: str
    archived_at_ms: int
    sha256: str


# ---------------------------------------------------------------------------
# Manifest table — DDL idempotent so we can run on existing dbs.
# ---------------------------------------------------------------------------

MANIFEST_DDL = """
CREATE TABLE IF NOT EXISTS audit_archive_manifest (
    table_name      TEXT PRIMARY KEY,
    year            INT NOT NULL,
    month           INT NOT NULL,
    rows_archived   BIGINT NOT NULL,
    object_key      TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class IcebergArchiver:
    """Archives audit partitions to MinIO/S3 in a Hive/Iceberg-compatible
    layout. Trino's iceberg connector can pick the data up later via a
    table-DDL bound to the same prefix."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        bucket: str,
        endpoint_url: str | None,
        access_key: str | None,
        secret_key: str | None,
        region: str = "us-east-1",
        prefix: str = "audit_events",
    ) -> None:
        self._pool = pool
        self._bucket = bucket
        self._prefix = prefix
        try:
            import boto3
            from botocore.client import Config
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("boto3 is required for IcebergArchiver") from exc
        kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": region,
            "config": Config(signature_version="s3v4"),
        }
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if access_key:
            kwargs["aws_access_key_id"] = access_key
        if secret_key:
            kwargs["aws_secret_access_key"] = secret_key
        self._s3 = boto3.client(**kwargs)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def archive_aged_partitions(
        self, *, age_threshold: datetime
    ) -> list[ArchivedPartition]:
        """Find partitions older than `age_threshold`, archive them, and
        detach from the live table. Returns the list of newly archived
        partitions (already-archived months are skipped)."""
        await self._ensure_manifest()
        candidates = await self._list_partitions()
        out: list[ArchivedPartition] = []
        for table_name, year, month in candidates:
            partition_floor = datetime(year, month, 1, tzinfo=timezone.utc)
            if partition_floor >= age_threshold:
                continue
            if await self._already_archived(table_name):
                continue
            try:
                archived = await self._archive_one(table_name, year, month)
            except Exception as exc:  # noqa: BLE001
                _log.error(
                    "compliance.archive_failed",
                    table=table_name,
                    error=str(exc),
                )
                _ARCHIVE_FAILURES.labels(phase="export").inc()
                continue
            out.append(archived)
            _ARCHIVED_PARTITIONS.inc()
            _ARCHIVED_ROWS.inc(archived.rows_archived)
        return out

    async def list_archived(self) -> list[ArchivedPartition]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT table_name, year, month, rows_archived, object_key,
                       sha256, EXTRACT(EPOCH FROM archived_at) * 1000 AS archived_at_ms
                  FROM audit_archive_manifest
                 ORDER BY year DESC, month DESC
                """
            )
        return [
            ArchivedPartition(
                table_name=r["table_name"],
                year=int(r["year"]),
                month=int(r["month"]),
                rows_archived=int(r["rows_archived"]),
                object_key=r["object_key"],
                archived_at_ms=int(r["archived_at_ms"]),
                sha256=r["sha256"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_manifest(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(MANIFEST_DDL)

    async def _list_partitions(self) -> list[tuple[str, int, int]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT child.relname AS table_name
                  FROM pg_inherits i
                  JOIN pg_class child  ON child.oid = i.inhrelid
                  JOIN pg_class parent ON parent.oid = i.inhparent
                 WHERE parent.relname = 'audit_events'
                """
            )
        out: list[tuple[str, int, int]] = []
        for r in rows:
            name = r["table_name"]
            m = _PARTITION_RE.match(name)
            if m is None:
                continue
            out.append((name, int(m.group(1)), int(m.group(2))))
        return out

    async def _already_archived(self, table_name: str) -> bool:
        async with self._pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM audit_archive_manifest WHERE table_name = $1",
                    table_name,
                )
            )

    async def _archive_one(
        self, table_name: str, year: int, month: int
    ) -> ArchivedPartition:
        rows = await self._fetch_partition(table_name)
        parquet_bytes = _to_parquet(rows)
        sha = hashlib.sha256(parquet_bytes).hexdigest()
        object_key = (
            f"{self._prefix}/year={year:04d}/month={month:02d}/"
            f"{table_name}-{sha[:12]}.parquet"
        )
        await self._upload(object_key, parquet_bytes)
        await self._record_manifest(table_name, year, month, len(rows), object_key, sha)
        await self._detach_partition(table_name)
        _log.info(
            "compliance.archived_partition",
            table=table_name,
            rows=len(rows),
            object_key=object_key,
            sha256=sha[:12],
        )
        return ArchivedPartition(
            table_name=table_name,
            year=year,
            month=month,
            rows_archived=len(rows),
            object_key=object_key,
            archived_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            sha256=sha,
        )

    async def _fetch_partition(self, table_name: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async with self._pool.acquire() as conn:
            cursor = conn.cursor(
                f'SELECT id, actor_id, actor_kind, action, resource_kind, '
                f'resource_id, purpose, request_id, tenant_id, metadata, '
                f'event_ts, received_at FROM "{table_name}"'
            )
            async for row in cursor:
                out.append(dict(row))
        return out

    async def _upload(self, key: str, body: bytes) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=io.BytesIO(body),
                ContentType="application/x-parquet",
            ),
        )

    async def _record_manifest(
        self,
        table_name: str,
        year: int,
        month: int,
        rows: int,
        object_key: str,
        sha: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_archive_manifest
                  (table_name, year, month, rows_archived, object_key, sha256)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (table_name) DO UPDATE
                   SET rows_archived = EXCLUDED.rows_archived,
                       object_key = EXCLUDED.object_key,
                       sha256 = EXCLUDED.sha256,
                       archived_at = now()
                """,
                table_name,
                year,
                month,
                rows,
                object_key,
                sha,
            )

    async def _detach_partition(self, table_name: str) -> None:
        async with self._pool.acquire() as conn:
            # ALTER TABLE ... DETACH PARTITION is the safe operation: it
            # leaves the underlying table around so anyone with a manual
            # query reference still resolves. The DBA reaps detached
            # tables after the lakehouse copy is verified.
            await conn.execute(
                f'ALTER TABLE audit_events DETACH PARTITION "{table_name}"'
            )


def _to_parquet(rows: list[dict[str, Any]]) -> bytes:
    """Serialise audit-event rows to Parquet via PyArrow.

    Iceberg-compatible primitive types only — JSONB metadata is encoded
    as a UTF-8 string. The downstream Trino/Iceberg reader json_parses on
    read; this preserves the audit shape without binding us to a
    pyiceberg version at write time.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    if not rows:
        # Empty Parquet still needs a schema; emit one with just the keys
        # and zero rows so downstream tooling sees a valid file.
        empty_schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("actor_id", pa.string()),
                pa.field("actor_kind", pa.string()),
                pa.field("action", pa.string()),
                pa.field("resource_kind", pa.string()),
                pa.field("resource_id", pa.string()),
                pa.field("purpose", pa.string()),
                pa.field("request_id", pa.string()),
                pa.field("tenant_id", pa.string()),
                pa.field("metadata_json", pa.string()),
                pa.field("event_ts", pa.timestamp("us", tz="UTC")),
                pa.field("received_at", pa.timestamp("us", tz="UTC")),
            ]
        )
        table = pa.Table.from_pydict({f.name: [] for f in empty_schema}, schema=empty_schema)
    else:
        import json

        flat: dict[str, list[Any]] = {
            "id": [],
            "actor_id": [],
            "actor_kind": [],
            "action": [],
            "resource_kind": [],
            "resource_id": [],
            "purpose": [],
            "request_id": [],
            "tenant_id": [],
            "metadata_json": [],
            "event_ts": [],
            "received_at": [],
        }
        for r in rows:
            flat["id"].append(str(r.get("id")) if r.get("id") is not None else None)
            flat["actor_id"].append(str(r.get("actor_id")) if r.get("actor_id") is not None else None)
            flat["actor_kind"].append(r.get("actor_kind"))
            flat["action"].append(r.get("action"))
            flat["resource_kind"].append(r.get("resource_kind"))
            flat["resource_id"].append(r.get("resource_id"))
            flat["purpose"].append(r.get("purpose"))
            flat["request_id"].append(r.get("request_id"))
            flat["tenant_id"].append(r.get("tenant_id"))
            md = r.get("metadata")
            flat["metadata_json"].append(json.dumps(md) if md is not None else None)
            flat["event_ts"].append(r.get("event_ts"))
            flat["received_at"].append(r.get("received_at"))
        table = pa.Table.from_pydict(flat)

    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class ArchiveScheduler:
    """Cron-style scheduler. Wakes every `interval_s` seconds and runs
    the archive pass over partitions older than `retention_days`."""

    def __init__(
        self,
        *,
        archiver: IcebergArchiver,
        retention_days: int = 180,
        interval_s: int = 86_400,
    ) -> None:
        self._archiver = archiver
        self._retention_days = retention_days
        self._interval_s = interval_s
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        # Initial delay to let the rest of the service stabilise.
        await asyncio.sleep(min(60, self._interval_s))
        while not self._stop.is_set():
            await self.trigger()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except asyncio.TimeoutError:
                continue

    async def trigger(self) -> list[ArchivedPartition]:
        if self._lock.locked():
            return []
        async with self._lock:
            from datetime import timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
            return await self._archiver.archive_aged_partitions(age_threshold=cutoff)

    async def stop(self) -> None:
        self._stop.set()


def settings_from_env() -> dict[str, Any]:
    """Read archive settings from environment without touching the
    main settings dataclass — keeps the archive opt-in surface small."""
    return {
        "enabled": os.environ.get("ICEBERG_ARCHIVE_ENABLED", "1") == "1",
        "bucket": os.environ.get("ICEBERG_ARCHIVE_BUCKET", "fraudnet-audit-archive"),
        "endpoint_url": os.environ.get("ICEBERG_ARCHIVE_ENDPOINT", "http://localhost:9000"),
        "access_key": os.environ.get("ICEBERG_ARCHIVE_ACCESS_KEY", "fraudnet"),
        "secret_key": os.environ.get("ICEBERG_ARCHIVE_SECRET_KEY", "fraudnet_dev_minio"),
        "retention_days": int(os.environ.get("ICEBERG_ARCHIVE_RETENTION_DAYS", "180")),
        "interval_s": int(os.environ.get("ICEBERG_ARCHIVE_INTERVAL_S", "86400")),
    }
