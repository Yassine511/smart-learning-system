#!/usr/bin/env bash
set -euo pipefail
cd /opt/smart-learning-local
exec docker compose --project-name smart-learning-local --env-file .env \
  -f compose.yaml -f compose.lab.yaml -f compose.local-prototype.yaml "$@"
