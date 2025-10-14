#!/usr/bin/env bash
set -euo pipefail

# Copy latest built plugin bundle (infra/plugin/dist/mm-importer-<version>.tar.gz)
# into infra/test-data/plugin-bundles/ and create/update a stable symlink latest.tar.gz
# so that PLUGIN_BUNDLE_URL can point to either versioned or stable URL.

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PLUGIN_DIR="$REPO_ROOT/infra/plugin"
DIST_DIR="$PLUGIN_DIR/dist"
DEST_DIR="$REPO_ROOT/infra/test-data/plugin-bundles"

shopt -s nullglob
bundles=("$DIST_DIR"/mm-importer-*.tar.gz)
if (( ${#bundles[@]} == 0 )); then
  echo "No bundles found in $DIST_DIR. Build first (build-docker.sh)." >&2
  exit 1
fi

# Pick the most recently modified
latest_bundle=$(ls -t "${bundles[@]}" | head -n1)
file_name=$(basename "$latest_bundle")
mkdir -p "$DEST_DIR"
cp -f "$latest_bundle" "$DEST_DIR/$file_name"
ln -sf "$file_name" "$DEST_DIR/latest.tar.gz"

echo "Published $file_name to test-files directory (and updated latest.tar.gz symlink)."
echo "Version URL: http://localhost:9000/plugin-bundles/$file_name"
echo "Latest URL : http://localhost:9000/plugin-bundles/latest.tar.gz"