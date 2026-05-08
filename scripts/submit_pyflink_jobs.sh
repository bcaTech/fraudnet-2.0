#!/usr/bin/env bash
#
# Submit the PyFlink jobs for stream-features and stream-graph to a Flink
# cluster. Defaults target the dev compose stack; override env to point
# at staging or prod.
#
# Usage:
#   FLINK_REST_HOST=flink-jobmanager FLINK_REST_PORT=8081 \\
#       scripts/submit_pyflink_jobs.sh [features|graph|all]
#
# Required env (with sensible dev defaults):
#   FLINK_REST_HOST            jobmanager hostname (default: localhost)
#   FLINK_REST_PORT            jobmanager REST port (default: 8081)
#   KAFKA_BOOTSTRAP_SERVERS    kafka bootstrap (default: kafka:29092)
#   SCHEMA_REGISTRY_URL        schema registry URL (default: http://schema-registry:8081)
#   FLINK_KAFKA_CONNECTOR_JAR  path on the jobmanager to the SQL connector jar

set -euo pipefail

TARGET="${1:-all}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_JOB="${REPO_ROOT}/services/stream-features/src/stream_features/pyflink_job.py"
GRAPH_JOB="${REPO_ROOT}/services/stream-graph/src/stream_graph/pyflink_job.py"

FLINK_BIN="${FLINK_BIN:-flink}"
FLINK_KAFKA_CONNECTOR_JAR="${FLINK_KAFKA_CONNECTOR_JAR:-/opt/flink/lib/flink-sql-connector-kafka.jar}"

submit() {
  local job_file="$1"
  local job_label="$2"
  echo "==> submitting ${job_label} (${job_file})"
  KAFKA_BOOTSTRAP_SERVERS="${KAFKA_BOOTSTRAP_SERVERS:-kafka:29092}" \
  SCHEMA_REGISTRY_URL="${SCHEMA_REGISTRY_URL:-http://schema-registry:8081}" \
  "${FLINK_BIN}" run \
      -d \
      -py "${job_file}" \
      -j "${FLINK_KAFKA_CONNECTOR_JAR}"
}

case "${TARGET}" in
  features) submit "${FEATURES_JOB}" "stream-features" ;;
  graph)    submit "${GRAPH_JOB}" "stream-graph" ;;
  all)
    submit "${FEATURES_JOB}" "stream-features"
    submit "${GRAPH_JOB}" "stream-graph"
    ;;
  *)
    echo "unknown target: ${TARGET} (use features|graph|all)" >&2
    exit 2
    ;;
esac
