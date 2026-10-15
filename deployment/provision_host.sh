#!/usr/bin/env bash
set -euo pipefail

ROOT=/srv/tiny-brain
USER_NAME=${1:-inigo}
USER_ID=$(id -u "$USER_NAME")
GROUP_ID=$(id -g "$USER_NAME")

install -d -o "$USER_ID" -g "$GROUP_ID" "$ROOT" "$ROOT/compose" "$ROOT/vault" "$ROOT/data" "$ROOT/models" "$ROOT/staging"
install -d -o "$USER_ID" -g "$GROUP_ID" "$ROOT/data/audit" "$ROOT/data/backups" "$ROOT/data/logs"
printf 'Provisioned %s for %s (%s:%s)\n' "$ROOT" "$USER_NAME" "$USER_ID" "$GROUP_ID"

# Optimization 6: Set CPU scaling governor to performance for fast turbo response on i7-8550U
if command -v cpupower &>/dev/null; then
    cpupower frequency-set -g performance || true
elif [ -d /sys/devices/system/cpu/cpu0/cpufreq ]; then
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        [ -f "$cpu" ] && echo performance > "$cpu" || true
    done
fi

printf 'Next: inspect Docker, Compose, Caddy, Syncthing, firewall, storage, and Git before importing the archive.\n'
