#!/usr/bin/env bash
# Create ./secrets/* for docker-compose.yml. Generates random passwords for Postgres, Redis and the admin API.
# Never prints a secret. Never overwrites an existing file. The Deriv PAT and Telegram token are created EMPTY: the owner
# pastes them into the files by hand (the dashboard password hash is set with scripts/set_dashboard_password.py) (an empty optional secret means "not provided": no Deriv session, alerts only logged).
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p secrets
for name in postgres_password redis_password admin_api_token; do
    f="secrets/$name"
    if [ -e "$f" ]; then echo "keep   $f"; continue; fi
    python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$f"
    echo "create $f (random)"
done
for name in deriv_pat_demo telegram_bot_token dashboard_password_hash; do
    f="secrets/$name"
    if [ -e "$f" ]; then echo "keep   $f"; continue; fi
    : > "$f"
    echo "create $f (EMPTY: owner fills in)"
done
# The containers run as non-root uids; compose bind-mounts the files read-only, so they must be world-readable inside
# the container. The directory itself stays 0700 on the host.
chmod 0644 secrets/*
chmod 0700 secrets
