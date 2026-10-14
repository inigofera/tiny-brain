#!/usr/bin/env bash
set -euo pipefail

ROOT=/srv/tiny-brain
USER_NAME=${1:-inigo}
USER_ID=$(id -u "$USER_NAME")
GROUP_ID=$(id -g "$USER_NAME")

install -d -o "$USER_ID" -g "$GROUP_ID" "$ROOT" "$ROOT/compose" "$ROOT/vault" "$ROOT/data" "$ROOT/models" "$ROOT/staging"
install -d -o "$USER_ID" -g "$GROUP_ID" "$ROOT/data/audit" "$ROOT/data/backups" "$ROOT/data/logs"
printf 'Provisioned %s for %s (%s:%s)\n' "$ROOT" "$USER_NAME" "$USER_ID" "$GROUP_ID"
printf 'Next: inspect Docker, Compose, Caddy, Syncthing, firewall, storage, and Git before importing the archive.\n'
