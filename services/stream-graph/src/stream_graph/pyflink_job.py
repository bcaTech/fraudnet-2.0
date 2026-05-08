"""PyFlink Table-API job for stream-graph.

The Phase-1 standalone runner reads voice/sms/momo Avro events and pushes
graph mutations into Memgraph using a buffered batch writer. The Phase-2
job translates each input event into a `GraphMutationV1` row and writes
to `graph.mutations.v1` via the upsert-kafka connector. A small
downstream worker (the existing GraphRunner can be configured to read
from `graph.mutations.v1` instead of the source topics) drains those
mutations into Memgraph, keeping the graph as the operational write
target while letting Flink own watermarking + horizontal scale.

Submit:
    flink run -py services/stream-graph/src/stream_graph/pyflink_job.py \\
        -d \\
        -j /opt/flink/lib/flink-sql-connector-kafka.jar
"""

from __future__ import annotations

import os


VOICE_TOPIC = "voice.events.v1"
SMS_TOPIC = "sms.events.v1"
MOMO_TOPIC = "momo.events.v1"
MUTATIONS_TOPIC = "graph.mutations.v1"


def _required(env: str) -> str:
    val = os.environ.get(env)
    if val is None:
        raise RuntimeError(f"{env} is required for the PyFlink job")
    return val


def _voice_source_ddl(*, bootstrap: str, schema_registry_url: str, group_id: str) -> str:
    return f"""
        CREATE TABLE voice_events (
            event_id STRING,
            event_ts_ms BIGINT,
            tenant_id STRING,
            kind STRING,
            caller STRING,
            callee STRING,
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


def _sms_source_ddl(*, bootstrap: str, schema_registry_url: str, group_id: str) -> str:
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


def _momo_source_ddl(*, bootstrap: str, schema_registry_url: str, group_id: str) -> str:
    return f"""
        CREATE TABLE momo_events (
            event_id STRING,
            event_ts_ms BIGINT,
            tenant_id STRING,
            kind STRING,
            txn_id STRING,
            sender_wallet_id STRING,
            recipient_wallet_id STRING,
            sender_msisdn STRING,
            recipient_msisdn STRING,
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


def _mutations_sink_ddl(*, bootstrap: str, schema_registry_url: str) -> str:
    return f"""
        CREATE TABLE graph_mutations_sink (
            event_id STRING,
            event_ts_ms BIGINT,
            ingest_ts_ms BIGINT,
            source STRING,
            tenant_id STRING,
            op STRING,
            node_kind STRING,
            node_id STRING,
            edge_kind STRING,
            src_kind STRING,
            src_id STRING,
            dst_kind STRING,
            dst_id STRING,
            properties MAP<STRING, STRING>
        ) WITH (
            'connector' = 'kafka',
            'topic' = '{MUTATIONS_TOPIC}',
            'properties.bootstrap.servers' = '{bootstrap}',
            'format' = 'avro-confluent',
            'avro-confluent.url' = '{schema_registry_url}'
        )
    """


# Voice → CALLED edges. Per call_start emit (caller, callee, CALLED).
_VOICE_TO_MUTATIONS = """
    INSERT INTO graph_mutations_sink
    SELECT
        event_id,
        event_ts_ms,
        UNIX_TIMESTAMP() * 1000 AS ingest_ts_ms,
        'stream-graph-flink' AS source,
        tenant_id,
        'upsert_edge' AS op,
        CAST(NULL AS STRING) AS node_kind,
        CAST(NULL AS STRING) AS node_id,
        'CALLED' AS edge_kind,
        'Number' AS src_kind,
        caller AS src_id,
        'Number' AS dst_kind,
        callee AS dst_id,
        MAP['ts', CAST(event_ts_ms AS STRING),
            'duration', CAST(coalesce(duration_s, 0) AS STRING)] AS properties
    FROM voice_events
    WHERE kind = 'call_start' AND callee IS NOT NULL
"""

# SMS → SMSED edges.
_SMS_TO_MUTATIONS = """
    INSERT INTO graph_mutations_sink
    SELECT
        event_id,
        event_ts_ms,
        UNIX_TIMESTAMP() * 1000 AS ingest_ts_ms,
        'stream-graph-flink' AS source,
        tenant_id,
        'upsert_edge' AS op,
        CAST(NULL AS STRING),
        CAST(NULL AS STRING),
        'SMSED',
        'Number',
        sender,
        'Number',
        recipient,
        MAP['ts', CAST(event_ts_ms AS STRING),
            'template_hash', coalesce(template_hash, '')]
    FROM sms_events
    WHERE kind IN ('mt', 'mo')
"""

# MoMo → wallet→wallet SENT edges (when both wallets present), or
# wallet→account CASHED_OUT_TO when the counterparty is a bank/external.
_MOMO_TO_MUTATIONS_SENT = """
    INSERT INTO graph_mutations_sink
    SELECT
        event_id,
        event_ts_ms,
        UNIX_TIMESTAMP() * 1000,
        'stream-graph-flink',
        tenant_id,
        'upsert_edge',
        CAST(NULL AS STRING),
        CAST(NULL AS STRING),
        'SENT',
        'Wallet',
        sender_wallet_id,
        'Wallet',
        recipient_wallet_id,
        MAP['ts', CAST(event_ts_ms AS STRING),
            'amount', CAST(amount_minor AS STRING)]
    FROM momo_events
    WHERE sender_wallet_id IS NOT NULL
      AND recipient_wallet_id IS NOT NULL
      AND kind IN ('p2p_transfer', 'merchant_payment', 'cash_out')
"""

_MOMO_TO_MUTATIONS_CASHED_OUT = """
    INSERT INTO graph_mutations_sink
    SELECT
        event_id,
        event_ts_ms,
        UNIX_TIMESTAMP() * 1000,
        'stream-graph-flink',
        tenant_id,
        'upsert_edge',
        CAST(NULL AS STRING),
        CAST(NULL AS STRING),
        'CASHED_OUT_TO',
        'Wallet',
        sender_wallet_id,
        'Account',
        counterparty_account_hash,
        MAP['ts', CAST(event_ts_ms AS STRING),
            'amount', CAST(amount_minor AS STRING)]
    FROM momo_events
    WHERE sender_wallet_id IS NOT NULL
      AND counterparty_account_hash IS NOT NULL
      AND counterparty_kind IN ('bank', 'external')
"""


def main() -> None:  # pragma: no cover — Flink cluster entrypoint
    try:
        from pyflink.table import EnvironmentSettings, TableEnvironment
    except ImportError as exc:
        raise SystemExit(
            "pyflink is not installed. Install via 'uv sync --group flink' or "
            "use FLINK_MODE=standalone."
        ) from exc

    bootstrap = _required("KAFKA_BOOTSTRAP_SERVERS")
    schema_registry = _required("SCHEMA_REGISTRY_URL")
    group_id = os.environ.get("STREAM_GRAPH_GROUP", "stream-graph-flink")

    settings = EnvironmentSettings.in_streaming_mode()
    t_env = TableEnvironment.create(settings)

    t_env.execute_sql(_voice_source_ddl(
        bootstrap=bootstrap, schema_registry_url=schema_registry, group_id=group_id
    ))
    t_env.execute_sql(_sms_source_ddl(
        bootstrap=bootstrap, schema_registry_url=schema_registry, group_id=group_id
    ))
    t_env.execute_sql(_momo_source_ddl(
        bootstrap=bootstrap, schema_registry_url=schema_registry, group_id=group_id
    ))
    t_env.execute_sql(_mutations_sink_ddl(
        bootstrap=bootstrap, schema_registry_url=schema_registry
    ))

    statements = t_env.create_statement_set()
    statements.add_insert_sql(_VOICE_TO_MUTATIONS)
    statements.add_insert_sql(_SMS_TO_MUTATIONS)
    statements.add_insert_sql(_MOMO_TO_MUTATIONS_SENT)
    statements.add_insert_sql(_MOMO_TO_MUTATIONS_CASHED_OUT)
    statements.execute().wait()


if __name__ == "__main__":  # pragma: no cover
    main()
