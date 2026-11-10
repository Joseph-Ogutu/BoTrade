#!/usr/bin/env bash
# Daily: evaluate every forward-test variant that has reached its registered N (once each; counts-only otherwise), then approve strategies
# with a recorded non-void PASS. Safe to run any time; changes nothing until a variant is ready. Runs on the VM from /opt/derivbot.
set -euo pipefail
cd "$(dirname "$0")/.."
D="docker compose --env-file vm.env -f docker-compose.yml -f docker-compose.lowmem.yml"
$D exec -T bot python -m derivbot.forward.report --evaluate
before=$(sha256sum config/strategies.json)
$D run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/config/strategies.json:/tmp/strategies.json" bot \
  python -m derivbot.forward.promote --strategies /tmp/strategies.json
if [ "$before" != "$(sha256sum config/strategies.json)" ]; then
  echo "approvals changed: $(cat config/strategies.json | tr -d '\n ')"
fi

# Start real trading automatically once a strategy is approved AND the owner has put the real credentials on the VM (scripts/real_setup.sh).
# This only starts the container. The container refuses to run unless the build lock, the approval file for this commit, the signed
# docs/live-readiness.md, the Gate 1 record and config/risk.real.json all hold; if one does not, it exits and the reason is logged here,
# and the next daily run tries again. Nothing here bypasses or edits any of those checks.
approved_count=$(python3 -c 'import json;print(len(json.load(open("config/strategies.json")).get("approved",{})))')
if [ "$approved_count" -gt 0 ] && [ -s secrets/deriv_pat_real ] && [ -s REAL_TRADING_APPROVED ]; then
  if [ "$before" != "$(sha256sum config/strategies.json)" ]; then
    $D --profile real up -d --force-recreate bot-real                      # approvals changed: restart so it reads them
  else
    $D --profile real up -d bot-real
  fi
  sleep 45
  echo "bot-real status: $($D --profile real ps --format '{{.Status}}' bot-real 2>&1 | head -1)"
  $D --profile real logs --tail 8 bot-real 2>&1 | cut -c1-200
else
  echo "real start skipped: approved=$approved_count, real token present=$([ -s secrets/deriv_pat_real ] && echo yes || echo no), approval file present=$([ -s REAL_TRADING_APPROVED ] && echo yes || echo no)"
fi
