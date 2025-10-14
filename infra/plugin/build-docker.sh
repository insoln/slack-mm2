#!/usr/bin/env bash
set -euo pipefail

# Multi-stage Docker build wrapper for Mattermost plugin
# Produces dist/mm-importer-<version>.tar.gz without polluting host with node_modules

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PLUGIN_ID=$(awk -F '"' '/"id"/ {print $4; exit}' plugin.json)
VERSION=$(awk -F '"' '/"version"/ {print $4; exit}' plugin.json)

if [[ -z "${PLUGIN_ID}" || -z "${VERSION}" ]]; then
  echo "Failed to parse plugin id/version from plugin.json" >&2
  exit 1
fi

OUT_DIR="dist"
rm -rf "$OUT_DIR" || true
mkdir -p "$OUT_DIR"

echo "[plugin-build] Building ${PLUGIN_ID} ${VERSION} via Docker multi-stage..."

: "${DOCKER_BUILDKIT:=1}"
export DOCKER_BUILDKIT

docker build \
  --progress=plain \
  -f "$SCRIPT_DIR/Dockerfile" \
  --target package \
  --output "type=local,dest=${OUT_DIR}" \
  "$SCRIPT_DIR"

echo "[plugin-build] Raw output tree (top level):"
ls -lah "$OUT_DIR" | head -n 40

# Find tarball anywhere under OUT_DIR (BuildKit local export dumps full stage root filesystem)
FOUND_TARBALL=$(find "$OUT_DIR" -maxdepth 4 -type f -name "${PLUGIN_ID}-${VERSION}.tar.gz" | head -n1 || true)
if [[ -z "$FOUND_TARBALL" ]]; then
  echo "ERROR: Tarball ${PLUGIN_ID}-${VERSION}.tar.gz not found under $OUT_DIR" >&2
  exit 1
fi

# Normalize location: copy to OUT_DIR root if not already there
TARGET_TARBALL="${OUT_DIR}/${PLUGIN_ID}-${VERSION}.tar.gz"
if [[ "$FOUND_TARBALL" != "$TARGET_TARBALL" ]]; then
  cp -f "$FOUND_TARBALL" "$TARGET_TARBALL"
fi

echo "[plugin-build] Success: $TARGET_TARBALL"