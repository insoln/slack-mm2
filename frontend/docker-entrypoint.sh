#!/usr/bin/env bash
# NOTE: Ensure file mode +x in git. If missing, compose build step will chmod again.
set -euo pipefail

APP_DIR=/app
WORK_DIR=/workspace

echo "[entrypoint] Preparing ephemeral workspace at $WORK_DIR"
rm -rf "$WORK_DIR" 2>/dev/null || true
mkdir -p "$WORK_DIR"
cp "$APP_DIR"/package.json "$WORK_DIR"/
cp "$APP_DIR"/package-lock.json "$WORK_DIR"/ 2>/dev/null || true

cd "$WORK_DIR"
echo "[entrypoint] Installing dependencies (ephemeral)"
npm ci --no-audit --no-fund

export NODE_PATH="$WORK_DIR/node_modules"
export PATH="$WORK_DIR/node_modules/.bin:$PATH"
export VITE_CACHE_DIR="$WORK_DIR/.vite-cache"
mkdir -p "$VITE_CACHE_DIR"

cd "$WORK_DIR"
	echo "[entrypoint] Creating symlinks to read-only source for live reload"
	ln -sfn /app/src src
	ln -sfn /app/index.html index.html

	echo "[entrypoint] Preparing isolated Vite config in workspace"
	cp /app/vite.config.js vite.config.js
	# Inject cacheDir directive if not already present
	grep -q 'cacheDir' vite.config.js || sed -i 's/defineConfig({/defineConfig({ cacheDir: ".vite-cache",/' vite.config.js

	echo "[entrypoint] Starting Vite dev server (root=$WORK_DIR; src symlink -> /app/src)"
	exec vite --host 0.0.0.0
