#!/usr/bin/env bash
set -euo pipefail

ROOT=/srv/tiny-brain
DEST=${1:?backup destination is required}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$DEST/$STAMP"
tar --xattrs --acls -czf "$DEST/$STAMP/vault.tar.gz" -C "$ROOT" vault
tar --xattrs --acls -czf "$DEST/$STAMP/data.tar.gz" -C "$ROOT" data
tar --xattrs --acls -czf "$DEST/$STAMP/compose.tar.gz" -C "$ROOT" compose
git -C "$ROOT/vault" bundle create "$DEST/$STAMP/vault.git.bundle" --all
sha256sum "$DEST/$STAMP"/* > "$DEST/$STAMP/SHA256SUMS"
printf 'Backup written to %s\n' "$DEST/$STAMP"
