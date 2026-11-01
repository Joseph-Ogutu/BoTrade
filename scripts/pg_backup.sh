#!/usr/bin/env bash
# Logical backup of the trading database (custom format), verified, optionally copied off-host.
# Runs pg_dump INSIDE the postgres container over its local socket, so no password is read or passed by this script.
#   PG_CONTAINER   container name            (default derivbot-postgres)
#   PG_USER/PG_DB  role and database          (default bot / derivbot)
#   BACKUP_DIR     where dumps land           (default ./data/pg-backups)
#   KEEP_LOCAL     dumps to keep locally      (default 14)
#   OFFHOST_REMOTE rclone remote path; if set, the dump is copied and checked there. A failure fails the job.
set -euo pipefail
cd "$(dirname "$0")/.."

PG_CONTAINER="${PG_CONTAINER:-derivbot-postgres}"
PG_USER="${PG_USER:-bot}"
PG_DB="${PG_DB:-derivbot}"
BACKUP_DIR="${BACKUP_DIR:-./data/pg-backups}"
KEEP_LOCAL="${KEEP_LOCAL:-14}"

mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/${PG_DB}_${stamp}.dump"

docker exec -u postgres "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --format=custom --no-owner > "$out.partial"
# Verify the archive is readable before it counts as a backup.
docker exec -i -u postgres "$PG_CONTAINER" pg_restore --list < "$out.partial" > /dev/null
mv "$out.partial" "$out"
( cd "$BACKUP_DIR" && sha256sum "$(basename "$out")" > "$(basename "$out").sha256" )

# Retention: newest KEEP_LOCAL dumps (and their checksums).
ls -1t "$BACKUP_DIR"/*.dump 2>/dev/null | tail -n +"$((KEEP_LOCAL + 1))" | while read -r old; do rm -f "$old" "$old.sha256"; done

if [ -n "${OFFHOST_REMOTE:-}" ]; then
    rclone copy "$BACKUP_DIR" "$OFFHOST_REMOTE" --include "*.dump" --include "*.dump.sha256"
    rclone check "$BACKUP_DIR" "$OFFHOST_REMOTE" --include "*.dump" --one-way
    date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/LAST_OFFHOST_OK"
fi
echo "backup ok: $out"
