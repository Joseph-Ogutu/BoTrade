#!/usr/bin/env bash
# OWNER ONLY. Run this yourself on the VM, in your own terminal:  ssh -t <vm> 'cd /opt/derivbot && scripts/real_setup.sh'
# It asks for the real Trade token (typed hidden, never echoed or logged), the real app id and the real account id, writes them where
# the real bot reads them, and creates the approval file for the commit that is deployed on the VM. It starts nothing.
# Start afterwards with the one command it prints. Re-run it after any redeploy: the approval file must name the deployed commit.
set -euo pipefail
cd "$(dirname "$0")/.."
D="docker compose --env-file vm.env -f docker-compose.yml -f docker-compose.lowmem.yml"

echo "REAL MONEY setup. You are the owner and you are doing this yourself."
read -r -s -p "Real account Trade token (hidden): " TOKEN; echo
read -r -p "Real app id: " APPID
read -r -p "Real account id (starts with ROT): " ACCT
case "$ACCT" in ROT*) ;; *) echo "That is not a real account id (must start with ROT). Nothing written."; exit 1;; esac
[ "${#TOKEN}" -ge 15 ] && [ -n "$APPID" ] || { echo "Token or app id looks empty. Nothing written."; exit 1; }
read -r -p "Your name for the approval record: " WHO
read -r -p "Type APPROVE to create the approval file for the deployed commit: " OK
[ "$OK" = "APPROVE" ] || { echo "Not approved. Nothing written."; exit 1; }

COMMIT="$($D run --rm --no-deps bot printenv DERIVBOT_COMMIT | tr -d '\r\n')"
case "$COMMIT" in *[!0-9a-f]*|"") echo "Cannot read the deployed commit from the image. Nothing written."; exit 1;; esac

umask 077
printf '%s' "$TOKEN" > secrets/deriv_pat_real
{ grep -v -E '^(DERIV_APP_ID_REAL|DERIV_ACCOUNT_ID_REAL|GIT_COMMIT|REAL_APPROVAL_FILE)=' vm.env || true
  echo "DERIV_APP_ID_REAL=$APPID"; echo "DERIV_ACCOUNT_ID_REAL=$ACCT"; echo "GIT_COMMIT=$COMMIT"
  echo "REAL_APPROVAL_FILE=$PWD/REAL_TRADING_APPROVED"; } > vm.env.new
mv vm.env.new vm.env
printf 'approved_on: %s\ncommit: %s\napproved_by: %s\n' "$(date -u +%F)" "$COMMIT" "$WHO" > REAL_TRADING_APPROVED
unset TOKEN
echo
echo "Written: secrets/deriv_pat_real, vm.env (real ids), REAL_TRADING_APPROVED for commit ${COMMIT:0:12}."
echo "The real bot still refuses to start until: a strategy is approved, docs/live-readiness.md is signed, the Gate 1 record exists,"
echo "and config/risk.real.json passes. To start (and see which of those is still missing):"
echo "  $D --profile real up -d bot-real && $D --profile real logs --tail 20 bot-real"
