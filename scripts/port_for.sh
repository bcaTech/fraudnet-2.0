#!/usr/bin/env bash
# Map a service name to its dev port. Single source of truth.
set -euo pipefail
case "${1:?service name required}" in
  api-public)    echo 8000 ;;
  api-noc)       echo 8010 ;;
  api-customer)  echo 8011 ;;
  api-admin)     echo 8012 ;;
  api-enterprise)echo 8013 ;;
  ingest-momo)   echo 8100 ;;
  ingest-voice)  echo 8101 ;;
  ingest-sms)    echo 8102 ;;
  ingest-data)   echo 8103 ;;
  ingest-intel)  echo 8104 ;;
  decisions)     echo 8200 ;;
  action-tier1)  echo 8201 ;;
  action-tier2)  echo 8202 ;;
  action-tier3)  echo 8203 ;;
  brain-behavioural) echo 8300 ;;
  brain-content) echo 8301 ;;
  brain-graph)   echo 8302 ;;
  compliance)    echo 8400 ;;
  feedback)      echo 8401 ;;
  *) echo "unknown service: $1" >&2; exit 1 ;;
esac
