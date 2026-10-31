#!/usr/bin/env bash
# Start the local Postgres and Redis used by the integration tests. Local only.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dev-local
if [ ! -s dev-local/postgres.password ]; then
  ( umask 077; head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24 > dev-local/postgres.password )
fi
docker compose -f docker-compose.dev.yml up -d --wait
echo "postgres: 127.0.0.1:55432  redis: 127.0.0.1:56379  (password file: dev-local/postgres.password, git-ignored)"
