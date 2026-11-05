#!/usr/bin/env bash
# Create a 4 GB swap file on a small VM. Idempotent. Run with sudo.
set -euo pipefail
SIZE_GB="${1:-4}"
if swapon --show | grep -q /swapfile; then echo "swap already active"; exit 0; fi
fallocate -l "${SIZE_GB}G" /swapfile
chmod 600 /swapfile
mkswap /swapfile >/dev/null
swapon /swapfile
grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl -w vm.swappiness=20 >/dev/null
echo 'vm.swappiness=20' > /etc/sysctl.d/99-derivbot-swap.conf
swapon --show
