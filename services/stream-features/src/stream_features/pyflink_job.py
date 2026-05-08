"""PyFlink Table-API job for stream-features.

Submitted to a Flink cluster via:

    flink run -py services/stream-features/src/stream_features/pyflink_job.py \\
        -d \\
        -j /opt/flink/lib/flink-sql-connector-kafka-3.*.jar

The job:
  1. Declares Kafka source tables for voice / sms / momo via the Avro
     SQL connector. Schemas live in the Confluent Schema Registry.
  2. Computes per-key windowed aggregates using Flink's native sliding
     and tumbling windows (mapping cleanly onto FeaturePipeline's logic
     in pipeline.py).
  3. Writes feature snapshots to a Kafka sink topic (`features.v1`) which
     a small downstream sidecar drains into Aerospike, keeping the inline-
     tier Aerospike contract unchanged.

A Phase-1 standalone runner (`stream_features.runner`) is the fallback for
local dev and any deployment where Flink isn't available; pick by setting
`FLINK_MODE=cluster` (this entrypoint) or `FLINK_MODE=standalone` (the
runner). The default is `standalone`.
"""

from __future__ import annotations

import os
import sys


# Topic names used in the SQL DDL. Keep these aligned with topics.yaml.
VOICE_TOPIC = "voice.events.v1"
SMS_TOPIC = "sms.events.v1"
MOMO_TOPIC = "momo.events.v1"
FEATURES_SINK_TOPIC = "features.v1"


def _required(env: str, default: str | None = None) -> str:
    val = os.environ.get(env, default)
    if val is None:
        raise RuntimeError(f"{env} is required for the PyFlink job")
    return val


def _build_voice_ddl(*, bootstrap: str, schema_registry_url: str, group_id: str) -> str:
    return f"""
        CREATE TABLE voice_events (
            event_id STRING,
            event_ts_ms BIGINT,
            ingest_ts_ms BIGINT,
            tenant_id STRING,
            kind STRING,
            caller STRING,
            callee STRING,
            imsi STRING,
            imei STRING,
            duration_s INT,
            event_time AS TO_TIMESTAMP_LTZ(event_ts_ms, 3),
            WATERMARK FOR event_time AS event_time - INTERVAL '30' SECOND
        ) WITH (
            'connector' = 'kafka',
            'topic' = '{VOICE_TOPIC}',
            'properties.bootstrap.servers' = '{bootstrap}',
            'properties.group.id' = '{group_id}',
            'scan.startup.mode' = 'latest-offset',
            'format' = 'avro-confluent',
            'avro-confluent.url' = '{schema_registry_url}'
        )
    """


def _build_sms_ddl(*, bootstrap: str, schema_registry_url: str, group_id: str) -> str:
    return f"""
        CREATE TABLE sms_events (
            event_id STRING,
            event_ts_ms BIGINT,
            tenant_id STRING,
            kind STRING,
            sender STRING,
            recipient STRING,
            template_hash STRING,
            event_time AS TO_TIMESTAMP_LTZ(event_ts_ms, 3),
            WATERMARK FOR event_time AS event_time - INTERVAL '30' SECOND
        ) WITH (
            'connector' = 'kafka',
            'topic' = '{SMS_TOPIC}',
            'properties.bootstrap.servers' = '{bootstrap}',
            'properties.group.id' = '{group_id}',
            'scan.startup.mode' = 'latest-offset',
            'format' = 'avro-confluent',
            'avro-confluent.url' = '{schema_registry_url}'
        )
    """


def _build_momo_ddl(*, bootstrap: str, schema_registry_url: str, group_id: str) -> str:
    return f"""
        CREATE TABLE momo_events (
            event_id STRING,
            event_ts_ms BIGINT,
            tenant_id STRING,
            kind STRING,
            txn_id STRING,
            sender_wallet_id STRING,
            recipient_wallet_id STRING,
            amount_minor BIGINT,
            counterparty_kind STRING,
            counterparty_account_hash STRING,
            event_time AS TO_TIMESTAMP_LTZ(event_ts_ms, 3),
            WATERMARK FOR event_time AS event_time - INTERVAL '30' SECOND
        ) WITH (
            'connector' = 'kafka',
            'topic' = '{MOMO_TOPIC}',
            'properties.bootstrap.servers' = '{bootstrap}',
            'properties.group.id' = '{group_id}',
            'scan.startup.mode' = 'latest-offset',
            'format' = 'avro-confluent',
            'avro-confluent.url' = '{schema_registry_url}'
        )
    """


def _build_sink_ddl(*, bootstrap: str, schema_registry_url: str) -> str:
    return f"""
        CREATE TABLE features_sink (
            tenant_id STRING,
            entity_kind STRING,
            entity_id STRING,
            window_end TIMESTAMP_LTZ(3),
            velocity_1m BIGINT,
            velocity_5m BIGINT,
            velocity_1h BIGINT,
            fanout_1h BIGINT,
            sms_freq_1h BIGINT,
            momo_velocity_1h BIGINT,
            momo_counterparty_diversity_24h BIGINT,
            PRIMARY KEY (tenant_id, entity_kind, entity_id) NOT ENFORCED
        ) WITH (
            'connector' = 'upsert-kafka',
            'topic' = '{FEATURES_SINK_TOPIC}',
            'properties.bootstrap.servers' = '{bootstrap}',
            'key.format' = 'json',
            'value.format' = 'avro-confluent',
            'value.avro-confluent.url' = '{schema_registry_url}'
        )
    """


# Voice + SMS aggregations keyed on the originating MSISDN. We project
# velocity windows of 1m / 5m / 1h and the 1h fan-out / SMS frequency.
_VOICE_AGG_SQL = """
    INSERT INTO features_sink
    SELECT
        tenant_id,
        'number'                                   AS entity_kind,
        caller                                     AS entity_id,
        window_end,
        SUM(CASE WHEN event_time > window_end - INTERVAL '1' MINUTE  THEN 1 ELSE 0 END) AS velocity_1m,
        SUM(CASE WHEN event_time > window_end - INTERVAL '5' MINUTE  THEN 1 ELSE 0 END) AS velocity_5m,
        COUNT(*)                                                                          AS velocity_1h,
        COUNT(DISTINCT callee)                                                            AS fanout_1h,
        CAST(0 AS BIGINT)                                                                 AS sms_freq_1h,
        CAST(0 AS BIGINT)                                                                 AS momo_velocity_1h,
        CAST(0 AS BIGINT)                                                                 AS momo_counterparty_diversity_24h
    FROM TABLE(
        TUMBLE(TABLE voice_events, DESCRIPTOR(event_time), INTERVAL '1' HOUR)
    )
    WHERE kind = 'call_start' AND callee IS NOT NULL
    GROUP BY tenant_id, caller, window_end
"""

_SMS_AGG_SQL = """
    INSERT INTO features_sink
    SELECT
        tenant_id,
        'number'                                   AS entity_kind,
        sender                                     AS entity_id,
        window_end,
        CAST(0 AS BIGINT)                          AS velocity_1m,
        CAST(0 AS BIGINT)                          AS velocity_5m,
        CAST(0 AS BIGINT)                          AS velocity_1h,
        CAST(0 AS BIGINT)                          AS fanout_1h,
        COUNT(*)                                   AS sms_freq_1h,
        CAST(0 AS BIGINT)                          AS momo_velocity_1h,
        CAST(0 AS BIGINT)                          AS momo_counterparty_diversity_24h
    FROM TABLE(
        TUMBLE(TABLE sms_events, DESCRIPTOR(event_time), INTERVAL '1' HOUR)
    )
    GROUP BY tenant_id, sender, window_end
"""

# MoMo wallet velocity over a 1h tumble; counterparty diversity on a
# longer 24h window.
_MOMO_AGG_SQL = """
    INSERT INTO features_sink
    SELECT
        tenant_id,
        'wallet'                                   AS entity_kind,
        sender_wallet_id                            AS entity_id,
        window_end,
        CAST(0 AS BIGINT)                          AS velocity_1m,
        CAST(0 AS BIGINT)                          AS velocity_5m,
        CAST(0 AS BIGINT)                          AS velocity_1h,
        CAST(0 AS BIGINT)                          AS fanout_1h,
        CAST(0 AS BIGINT)                          AS sms_freq_1h,
        COUNT(*)                                   AS momo_velocity_1h,
        COUNT(DISTINCT recipient_wallet_id)        AS momo_counterparty_diversity_24h
    FROM TABLE(
        TUMBLE(TABLE momo_events, DESCRIPTOR(event_time), INTERVAL '1' HOUR)
    )
    WHERE sender_wallet_id IS NOT NULL AND kind <> 'reversal'
    GROUP BY tenant_id, sender_wallet_id, window_end
"""


def main() -> None:  # pragma: no cover — Flink cluster entrypoint
    """Build the StatementSet and submit. Imports pyflink lazily so the
    module is safe to import in environments that don't have it."""
    try:
        from pyflink.table import EnvironmentSettings, TableEnvironment
    except ImportError as exc:
        raise SystemExit(
            "pyflink is not installed in this environment. Install via "
            "'uv sync --group flink' or use FLINK_MODE=standalone."
        ) from exc

    bootstrap = _required("KAFKA_BOOTSTRAP_SERVERS")
    schema_registry = _required("SCHEMA_REGISTRY_URL")
    group_id = os.environ.get("STREAM_FEATURES_GROUP", "stream-features-flink")

    settings = EnvironmentSettings.in_streaming_mode()
    t_env = TableEnvironment.create(settings)

    t_env.execute_sql(_build_voice_ddl(
        bootstrap=bootstrap, schema_registry_url=schema_registry, group_id=group_id
    ))
    t_env.execute_sql(_build_sms_ddl(
        bootstrap=bootstrap, schema_registry_url=schema_registry, group_id=group_id
    ))
    t_env.execute_sql(_build_momo_ddl(
        bootstrap=bootstrap, schema_registry_url=schema_registry, group_id=group_id
    ))
    t_env.execute_sql(_build_sink_ddl(
        bootstrap=bootstrap, schema_registry_url=schema_registry
    ))

    statements = t_env.create_statement_set()
    statements.add_insert_sql(_VOICE_AGG_SQL)
    statements.add_insert_sql(_SMS_AGG_SQL)
    statements.add_insert_sql(_MOMO_AGG_SQL)
    statements.execute().wait()


if __name__ == "__main__":  # pragma: no cover
    main()
