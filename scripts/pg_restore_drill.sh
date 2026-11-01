#!/usr/bin/env bash
# Restore drill: restore a dump into a SCRATCH database, compare row counts with the source, check that the append-only
# audit triggers survived, then drop the scratch database. Exit 0 only if everything matched. Never touches the source.
#   usage: scripts/pg_restore_drill.sh <dump-file>
#   PG_CONTAINER / PG_USER / PG_DB as in pg_backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

dump="${1:?usage: pg_restore_drill.sh <dump-file>}"
PG_CONTAINER="${PG_CONTAINER:-derivbot-postgres}"
PG_USER="${PG_USER:-bot}"
PG_DB="${PG_DB:-derivbot}"
scratch="restore_drill_$(date -u +%Y%m%d%H%M%S)"

if [ -f "$dump.sha256" ]; then ( cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256" ); fi

psql_src() { docker exec -u postgres "$PG_CONTAINER" psql -U "$PG_USER" -d "$1" -v ON_ERROR_STOP=1 -At -c "$2"; }
cleanup() { docker exec -u postgres "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c "DROP DATABASE IF EXISTS $scratch" > /dev/null 2>&1 || true; }
trap cleanup EXIT

docker exec -u postgres "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $scratch" > /dev/null
docker exec -i -u postgres "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$scratch" --no-owner --exit-on-error < "$dump"

count_sql="SELECT string_agg(relname || '=' || n, ',' ORDER BY relname) FROM (
  SELECT c.relname, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text AS n
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind = 'r' AND n.nspname = 'public') t"
src_counts="$(psql_src "$PG_DB" "$count_sql")"
dst_counts="$(psql_src "$scratch" "$count_sql")"
echo "source : $src_counts"
echo "restore: $dst_counts"
[ "$src_counts" = "$dst_counts" ] || { echo "DRILL FAILED: row counts differ"; exit 1; }

trig="SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid = 'audit_events'::regclass"
[ "$(psql_src "$scratch" "$trig")" = "$(psql_src "$PG_DB" "$trig")" ] || { echo "DRILL FAILED: audit triggers differ"; exit 1; }
[ "$(psql_src "$scratch" "$trig")" -ge 1 ] || { echo "DRILL FAILED: no audit triggers in the restore"; exit 1; }
echo "DRILL OK ($scratch dropped on exit)"
