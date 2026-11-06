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
  echo "approvals changed: restarting bot-real if it is running"
  $D --profile real ps --status running --services 2>/dev/null | grep -qx bot-real && $D --profile real restart bot-real || true
fi
