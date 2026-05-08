#!/usr/bin/env bash
# Apply infra/kafka-topics/topics.yaml to the local Kafka cluster.
# Idempotent: missing topics are created, existing topics are left alone.
# Production deployment uses a Kafka operator (Strimzi / Confluent for K8s),
# not this script — keep that in mind before extending it.

set -euo pipefail

TOPICS_FILE="${TOPICS_FILE:-infra/kafka-topics/topics.yaml}"
BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:9092}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.dev.yml}"

if ! command -v yq >/dev/null 2>&1; then
  echo "yq not found — install with: brew install yq" >&2
  exit 1
fi

echo "Applying topics from ${TOPICS_FILE} → ${BOOTSTRAP}"

default_rf=$(yq '.defaults.replication_factor' "${TOPICS_FILE}")
default_configs=$(yq '.defaults.config // {} | to_entries | map("--config " + .key + "=" + (.value | tostring)) | join(" ")' "${TOPICS_FILE}")

count=$(yq '.topics | length' "${TOPICS_FILE}")
for i in $(seq 0 $((count - 1))); do
  name=$(yq ".topics[$i].name" "${TOPICS_FILE}")
  partitions=$(yq ".topics[$i].partitions" "${TOPICS_FILE}")
  rf=$(yq ".topics[$i].replication_factor // ${default_rf}" "${TOPICS_FILE}")
  configs=$(yq ".topics[$i].config // {} | to_entries | map(\"--config \" + .key + \"=\" + (.value | tostring)) | join(\" \")" "${TOPICS_FILE}")

  ${COMPOSE} exec -T kafka kafka-topics \
    --bootstrap-server kafka:9092 \
    --create --if-not-exists \
    --topic "${name}" \
    --partitions "${partitions}" \
    --replication-factor "${rf}" \
    ${default_configs} ${configs} \
    && echo "  ✓ ${name}" \
    || echo "  ✗ ${name} (failed)"
done

echo "Done."
