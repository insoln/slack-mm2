#!/usr/bin/env bash
# Portable plugin build: builds server (linux/amd64), webapp (if present), and bundles dist/<id>-<version>.tar.gz
set -euo pipefail

# Ensure we run from this script's directory (repo/infra/plugin)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Prefer Go from /usr/local/go if present
export PATH=/usr/local/go/bin:$PATH

echo "PWD: $(pwd)"
ls -la

# Tooling checks
if ! command -v go >/dev/null 2>&1; then
	echo "ERROR: Go toolchain not found (go). Install Go or run via docker compose mm-plugin-build." >&2
	exit 1
fi
which go && go version

# Read manifest (use Node if present; fallback to Python3)
if command -v node >/dev/null 2>&1; then
	PLUGIN_ID=$(node -e 'console.log(require("./plugin.json").id)')
	VERSION=$(node -e 'console.log(require("./plugin.json").version)')
elif command -v python3 >/dev/null 2>&1; then
	PLUGIN_ID=$(python3 - "$SCRIPT_DIR" <<'PY'
import json,sys
with open('plugin.json','r',encoding='utf-8') as f:
		data=json.load(f)
print(data.get('id',''))
PY
)
	VERSION=$(python3 - "$SCRIPT_DIR" <<'PY'
import json,sys
with open('plugin.json','r',encoding='utf-8') as f:
		data=json.load(f)
print(data.get('version',''))
PY
)
else
	echo "ERROR: Need node or python3 to parse plugin.json." >&2
	exit 1
fi

if [[ -z "$PLUGIN_ID" || -z "$VERSION" ]]; then
	echo "ERROR: Failed to get id/version from plugin.json" >&2
	exit 1
fi

echo "Building plugin ${PLUGIN_ID} version ${VERSION}"
export GOFLAGS="-buildvcs=false"
echo "GOFLAGS=$GOFLAGS"

# Build server (linux/amd64)
if [[ -d server ]]; then
	pushd server >/dev/null
	mkdir -p dist
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o dist/plugin-linux-amd64
	popd >/dev/null
else
	echo "ERROR: server directory not found" >&2
	exit 1
fi

# Build webapp if present
if [[ -d webapp && -f webapp/package.json ]]; then
	pushd webapp >/dev/null
	if command -v npm >/dev/null 2>&1; then
		npm ci --no-fund --no-audit
		npm run build
	else
		echo "WARN: npm not found; skipping webapp build" >&2
	fi
	popd >/dev/null
else
	echo "INFO: no webapp found; skipping"
fi

# Bundle
rm -rf dist || true
mkdir -p "dist/${PLUGIN_ID}/server" "dist/${PLUGIN_ID}/webapp"
cp -r server/dist "dist/${PLUGIN_ID}/server/"
if [[ -d webapp/dist ]]; then
	cp -r webapp/dist "dist/${PLUGIN_ID}/webapp/"
fi
[[ -d public ]] && cp -r public "dist/${PLUGIN_ID}/" || true
[[ -d assets ]] && cp -r assets "dist/${PLUGIN_ID}/" || true
cp plugin.json "dist/${PLUGIN_ID}/plugin.json"

tar -C dist -czf "dist/${PLUGIN_ID}-${VERSION}.tar.gz" "${PLUGIN_ID}"

# Adjust ownership if running as root (inside container). Ignore errors otherwise.
if [[ "$(id -u)" -eq 0 ]]; then
	chown -R 1000:1000 dist || true
fi

ls -lah dist
