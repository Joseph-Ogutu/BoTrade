#!/usr/bin/env bash
# Daily backup of the collector's SQLite file, copied off the host and verified there.
# Run by deploy/derivbot-backup.timer on the VM. Needs rclone configured for OFFHOST_REMOTE
# (credentials live in the deploy user's rclone config, outside this repo).
set -euo pipefail
cd "$(dirname "$0")/.."

: "${OFFHOST_REMOTE:?set OFFHOST_REMOTE (an rclone remote path) in /etc/derivbot/backup.env}"
KEEP_LOCAL="${KEEP_LOCAL:-14}"

# 1. Consistent snapshot taken inside the container with SQLite's online backup API, then verified.
docker compose exec -T collector python -m derivbot.collector.backup create \
    --db /data/ticks.sqlite --out /data/backups --keep "$KEEP_LOCAL"

# 2. Copy off-host, then compare checksums on the remote side. A failure here must fail the job.
rclone copy ./data/backups "$OFFHOST_REMOTE" --include "*.sqlite.gz" --include "*.sqlite.gz.sha256"
rclone check ./data/backups "$OFFHOST_REMOTE" --include "*.sqlite.gz" --one-way

# 3. Marker for monitoring: only written after the off-host copy verified.
date -u +%Y-%m-%dT%H:%M:%SZ > ./data/backups/LAST_OFFHOST_OK
echo "backup ok: $(ls -1t ./data/backups/*.sqlite.gz | head -1)"
