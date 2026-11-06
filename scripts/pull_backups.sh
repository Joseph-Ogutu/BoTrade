#!/usr/bin/env bash
# Run on the OWNER'S COMPUTER (not the VM): pull the VM's backups to a local folder and verify every checksum there.
# Off-host copy without any cloud account. Usage: scripts/pull_backups.sh   (env: VM, KEY, DEST override the defaults)
set -euo pipefail
VM="${VM:-deploy@84.12.90.146}"
KEY="${KEY:-$HOME/.ssh/oracle_deriv_bot_vm.key}"
DEST="${DEST:-$HOME/Backups/derivbot}"
mkdir -p "$DEST/pg-backups" "$DEST/tick-backups"
SSH="ssh -o IdentitiesOnly=yes -i $KEY"
rsync -a -e "$SSH" "$VM:/opt/derivbot/data/pg-backups/" "$DEST/pg-backups/"
rsync -a -e "$SSH" "$VM:/opt/derivbot/data/backups/" "$DEST/tick-backups/" 2>/dev/null || echo "no tick backups on the VM yet"
bad=0
for f in "$DEST"/pg-backups/*.sha256; do
  [ -e "$f" ] || continue
  (cd "$DEST/pg-backups" && sha256sum -c "$(basename "$f")" >/dev/null 2>&1) || { echo "CHECKSUM FAILED: $f"; bad=1; }
done
[ "$bad" = 0 ] || exit 1
date -u +%Y-%m-%dT%H:%M:%SZ > "$DEST/LAST_PULL_OK"
echo "backups pulled and verified into $DEST ($(ls "$DEST"/pg-backups/*.dump 2>/dev/null | wc -l) database dumps)"
