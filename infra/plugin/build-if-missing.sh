#!/usr/bin/env sh
set -eu

# Best-practice enhancements:
# - Use flock-like directory creation to avoid concurrent duplicate builds
# - Produce SHA256 checksum alongside tarball
# - Update latest.tar.gz symlink atomically
# - Only install nodejs if webapp exists
# - Emit clear phase logs for observability

log() { printf '[plugin-autobuild] %s\n' "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

PLUGIN_JSON="/repo/infra/plugin/plugin.json"
[ -f "$PLUGIN_JSON" ] || fail "plugin.json not found at $PLUGIN_JSON"

extract_field() {
  awk -v key="$1" -F '"' '$2==key {print $4; exit}' "$PLUGIN_JSON"
}

ID=$(extract_field id || true)
VER=$(extract_field version || true)
[ -n "$ID" ] && [ -n "$VER" ] || fail "cannot parse id/version"

OUT_DIR="/out"
TARGET_TGZ="$OUT_DIR/${ID}-${VER}.tar.gz"
CHECKSUM_FILE="$TARGET_TGZ.sha256"

if [ -f "$TARGET_TGZ" ]; then
  log "Bundle present: $(basename "$TARGET_TGZ") — skip build"
  if [ ! -e "$OUT_DIR/latest.tar.gz" ]; then
    ln -s "${ID}-${VER}.tar.gz" "$OUT_DIR/latest.tar.gz" 2>/dev/null || true
  fi
  if [ ! -f "$CHECKSUM_FILE" ]; then
    (sha256sum "$TARGET_TGZ" | awk '{print $1"  '"$(basename "$TARGET_TGZ")"'"}' > "$CHECKSUM_FILE" 2>/dev/null) || true
  fi
  exit 0
fi

log "Need build: ${ID} ${VER} (missing $(basename "$TARGET_TGZ"))"

# Prepare work dir (tmpfs/layer)
WORK_BASE=/tmp/plugin-build
WORK="$WORK_BASE/work"
LOCKDIR="$WORK_BASE/.lock-${ID}-${VER}"
rm -rf "$WORK_BASE" && mkdir -p "$WORK_BASE"

# Simple directory lock to avoid duplicate builds (best-effort)
if mkdir "$LOCKDIR" 2>/dev/null; then
  :
else
  log "Another build appears in progress; waiting briefly"
  for i in 1 2 3 4 5; do
    [ -f "$TARGET_TGZ" ] && log "Detected completed build during wait" && exit 0
    sleep 1
  done
  [ -f "$TARGET_TGZ" ] && log "Completed by other process; exiting" && exit 0
  log "Lock still held; proceeding anyway"
fi

mkdir -p "$WORK"
cp -r /repo/infra/plugin "$WORK/plugin"
cd "$WORK/plugin"

# Ensure toolchains present (image may not have go / node).
apk add --no-cache git ca-certificates build-base >/dev/null 2>&1 || true
if [ -f webapp/package.json ] && ! command -v npm >/dev/null 2>&1; then
  log "Installing nodejs (webapp detected)"
  apk add --no-cache nodejs npm || fail "apk node install failed"
fi

log "Build server (go)"
mkdir -p server/dist
# NOTE: We add -buildvcs=false because the repo is mounted read-only (and may appear as an 'unsafe' ownership to git inside the container),
# which caused: 'error obtaining VCS status: exit status 128'. Disabling VCS stamping avoids git introspection failures.
# (Alternative would be: git config --global --add safe.directory /repo)
( cd server && \
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath -ldflags='-s -w' -o dist/plugin-linux-amd64 . \
) || fail "go build failed"

if [ -f webapp/package.json ]; then
  log "Build webapp"
  ( cd webapp && npm ci --no-audit --no-fund && npm run build ) || fail "webapp build failed"
else
  log "No webapp; skip"
fi

PKG_ROOT="$WORK/pkg"; PKG_DIR="$PKG_ROOT/$ID"
mkdir -p "$PKG_DIR/server/dist" "$PKG_DIR/webapp"
cp plugin.json "$PKG_DIR/"
cp server/dist/plugin-linux-amd64 "$PKG_DIR/server/dist/"
[ -d webapp/dist ] && cp -r webapp/dist "$PKG_DIR/webapp/" || true
[ -d assets ] && cp -r assets "$PKG_DIR/" || true
[ -d public ] && cp -r public "$PKG_DIR/" || true

mkdir -p "$OUT_DIR"
tar -C "$PKG_ROOT" -czf "$TARGET_TGZ" "$ID" || fail "tar failed"
sha256sum "$TARGET_TGZ" | awk '{print $1"  '"$(basename "$TARGET_TGZ")"'"}' > "$CHECKSUM_FILE" || log "WARN: checksum generation failed"
TMP_LINK="$OUT_DIR/.latest.tmp"
ln -sf "${ID}-${VER}.tar.gz" "$TMP_LINK" && mv -f "$TMP_LINK" "$OUT_DIR/latest.tar.gz" || true

log "Created $(basename "$TARGET_TGZ") (sha256: $(cut -d' ' -f1 "$CHECKSUM_FILE" 2>/dev/null || echo unknown))"
log "Done"