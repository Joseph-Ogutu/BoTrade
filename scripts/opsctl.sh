#!/usr/bin/env bash
# Run the ops CLI inside the bot container (the admin API is loopback-only inside it and unpublished).
# Usage: scripts/opsctl.sh status | kill <reason> | release <reason>
set -euo pipefail
cd "$(dirname "$0")/.."
exec docker compose exec -T bot python -m derivbot.opsctl "$@"
