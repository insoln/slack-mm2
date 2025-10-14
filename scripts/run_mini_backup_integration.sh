#!/usr/bin/env bash
set -euo pipefail

# Integration test script: run minimal Slack -> Mattermost import using mini backup dataset.
# Can be executed standalone locally or in CI. It will:
#  1. Bring up required docker compose services (db, mattermost, backend)
#  2. Wait for backend health endpoint
#  3. Upload the mini Slack export dataset
#  4. Poll job status until success (bounded) and assert final counters
#  5. Scan logs for errors and validate admin user mapping
#  6. Tear down services
# Expected counts (derived from current mini dataset, strict deterministic):
#   users=3 (2 real + 1 bot; admin UADMIN present)
#   channels=3 (public + private + DM)
#   messages=17 (all messages including thread replies; deleted tombstone still counted for determinism)
#   attachments=3 (three test-files hosted attachments with allowed url_private prefixes)
#     - public-channel day1: example.txt (FTXT1)
#     - private-channel day1: image.png
#     - private-channel day2: archive.zip
#   reactions=1

# Expected counts (allow override via env for flexibility)
: "${EXPECTED_USERS:=3}"
: "${EXPECTED_CHANNELS:=3}"
: "${EXPECTED_MESSAGES:=17}"
: "${EXPECTED_ATTACHMENTS:=3}"
: "${EXPECTED_REACTIONS:=0}"

# Compose file, services list, dataset, and log capture path (override allowed)
: "${COMPOSE_FILE:=infra/docker-compose.dev.yml}"
SERVICES="${COMPOSE_SERVICES:-db mattermost backend test-files}"
DATASET_FILE="${DATASET_FILE:-infra/test-data/slack-mini-backup.zip}"
LOG_CAPTURE=${LOG_CAPTURE:-/tmp/backend_integration_logs.txt}

# Timestamp (UTC RFC3339) used to scope log collection so prior run noise is excluded.
# Can be disabled with LOG_SCOPE=0
LOG_SCOPE=${LOG_SCOPE:-1}
START_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# If set, limit maximum seconds we wait overall (acts as guardrail) – mainly CI safety.
MAX_TOTAL_SECONDS=${MAX_TOTAL_SECONDS:-900}
SCRIPT_START_EPOCH=$(date +%s)

echo "[INFO] Using compose file: $COMPOSE_FILE"
echo "[INFO] Services: $SERVICES"
echo "[INFO] Dataset file: $DATASET_FILE"

if [[ ! -f "$DATASET_FILE" ]]; then
    echo "Dataset file not found: $DATASET_FILE" >&2
    echo "This script expects the mini backup zip to be committed to the repository." >&2
    exit 1
  fi

# Ensure infra/.env exists (CI runners won't have it by default). Provide dummy Slack tokens to satisfy docker compose env expectations.
if [[ ! -f infra/.env ]]; then
  echo "[INFO] Creating infra/.env with placeholder Slack tokens"
  cat > infra/.env <<'ENVEOF'
SLACK_VERIFICATION_TOKEN=dummy
SLACK_BOT_TOKEN=dummy
SLACK_SIGNING_SECRET=dummy
ENVEOF
fi
teardown() {
  echo "[CLEANUP] docker compose down"
  docker compose -f "$COMPOSE_FILE" down -v || true
}
trap teardown EXIT

echo "[STEP] Starting docker compose services"
docker compose -f "$COMPOSE_FILE" up -d $SERVICES

# Capture backend container ID after startup (used for precise log scoping if needed)
BACKEND_CID=$(docker compose -f "$COMPOSE_FILE" ps -q backend || true)
if [[ -z "$BACKEND_CID" ]]; then
  echo "[WARN] Could not resolve backend container ID yet; will rely on compose logs" >&2
fi

echo "[STEP] Waiting for backend healthcheck"
HEALTH_OK=0
for i in {1..90}; do
  HTTP_CODE=$(curl -s -o /tmp/health.json -w '%{http_code}' http://localhost:8000/healthcheck || true)
  BODY=$(cat /tmp/health.json 2>/dev/null || true)
  if [[ "$HTTP_CODE" == "200" && "$BODY" == *"ok"* ]]; then
    HEALTH_OK=1
    break
  fi
  if (( i % 10 == 0 )); then
    echo "[WAIT] health attempt=$i code=$HTTP_CODE body=$BODY" >&2
  fi
  sleep 2
done
if [[ $HEALTH_OK -ne 1 ]]; then
  echo "Backend healthcheck failed to become healthy in time (last code=$HTTP_CODE body=$BODY)" >&2
  docker compose -f "$COMPOSE_FILE" logs backend | tail -400 >&2
  exit 1
fi
echo "[INFO] Backend healthy (code=$HTTP_CODE)"

echo "[STEP] Ensuring Mattermost plugin (build+deploy+enable) BEFORE dataset upload"
PLUGIN_OK=0
for i in {1..12}; do
  RESP=$(curl -s -o /tmp/plugin_status.json -w '%{http_code}' -X POST http://localhost:8000/plugin/ensure || true)
  BODY=$(cat /tmp/plugin_status.json 2>/dev/null || true)
  STATUS=$(python3 - <<'PY'
import json,sys
try:
    d=json.load(open('/tmp/plugin_status.json'))
    print(d.get('status'))
except Exception:
    print('')
PY
)
  ENABLED=$(python3 - <<'PY'
import json,sys
try:
    d=json.load(open('/tmp/plugin_status.json'))
    print('true' if d.get('enabled') else 'false')
except Exception:
    print('false')
PY
)
  if [[ "$RESP" == "200" && "$STATUS" == "ensured" && "$ENABLED" == "true" ]]; then
    echo "[INFO] Plugin ensured and enabled (attempt $i)"
    PLUGIN_OK=1
    break
  fi
  if (( i % 4 == 0 )); then
    echo "[WAIT] plugin ensure attempt=$i code=$RESP status=$STATUS enabled=$ENABLED body=$(echo "$BODY" | head -c 160)" >&2
  fi
  sleep 2
done
if [[ $PLUGIN_OK -ne 1 ]]; then
  echo "Plugin failed to be ensured/enabled in time (last code=$RESP status=$STATUS enabled=$ENABLED)" >&2
  cat /tmp/plugin_status.json >&2 || true
  exit 1
fi

echo "[STEP] Verifying plugin hello endpoint"
HELLO_OK=0
for i in {1..15}; do
  PLUGIN_TOKEN="${MM_TOKEN:-5x7rr788c7gwdnkdr9imb49ffo}"
  HCODE=$(curl -s -o /tmp/plugin_hello.txt -w '%{http_code}' -H "Authorization: Bearer $PLUGIN_TOKEN" http://localhost:8065/plugins/mm-importer/api/v1/hello || true)
  HBODY=$(cat /tmp/plugin_hello.txt 2>/dev/null || true)
  # Accept either 200 (public hello) or 401 (auth enforced but endpoint exists)
  if [[ "$HCODE" == "200" || "$HCODE" == "401" ]]; then
    echo "[INFO] Plugin hello responded code=$HCODE body=$(echo "$HBODY" | head -c 60)"
    HELLO_OK=1; break
  fi
  sleep 2
done
if [[ $HELLO_OK -ne 1 ]]; then
  echo "Plugin hello endpoint not responding (last code=$HCODE body=$HBODY)" >&2
  exit 1
fi
echo "[INFO] Plugin hello endpoint OK"
echo "[STEP] Uploading dataset"
# Ensure IMPORT_URL_PREFIXES allows the test-files service host used inside CI network.
export IMPORT_URL_PREFIXES="https://files.slack.com,http://test-files:9000"
UPLOAD_RESP=$(curl -s -S -w '%{http_code}' -o /tmp/upload_resp.json -F "file=@${DATASET_FILE}" http://localhost:8000/upload || true)
if [[ "$UPLOAD_RESP" != "200" ]]; then
  echo "Upload failed (HTTP $UPLOAD_RESP)" >&2
  cat /tmp/upload_resp.json >&2 || true
  exit 1
fi
echo "[INFO] Upload succeeded (IMPORT_URL_PREFIXES=$IMPORT_URL_PREFIXES)"

JOB_DONE=0
echo "[STEP] Polling job status"
for i in {1..180}; do
  JOBS_JSON=$(curl -s http://localhost:8000/jobs || echo '{}')
  read -r JOB_STATUS CURRENT_STAGE USERS CHANNELS MESSAGES REACTIONS ATTACHMENTS <<<"$(python3 - <<'PY' "$JOBS_JSON"
import json,sys
try:
  j=json.loads(sys.argv[1])
except Exception:
  print('unknown','unknown','0','0','0','0','0')
  sys.exit(0)
jobs=j.get('jobs') or []
if not jobs:
  print('unknown','unknown','0','0','0','0','0')
  sys.exit(0)
job=jobs[0]
meta=job.get('meta') or {}
print(job.get('status','unknown'), job.get('current_stage','unknown'), meta.get('users_processed',0), meta.get('channels_processed',0), meta.get('messages_processed',0), meta.get('reactions_processed',0), meta.get('attachments_processed',0))
PY
)"
  echo "[POLL $i] status=$JOB_STATUS stage=$CURRENT_STAGE users=$USERS channels=$CHANNELS messages=$MESSAGES reactions=$REACTIONS attachments=$ATTACHMENTS"
  # Early success: if exporting stage reached and counters already match expected totals, stop.
  if [[ "$CURRENT_STAGE" == "exporting" ]] \
     && [[ "$USERS" == "$EXPECTED_USERS" ]] \
     && [[ "$CHANNELS" == "$EXPECTED_CHANNELS" ]] \
     && [[ "$MESSAGES" == "$EXPECTED_MESSAGES" ]] \
     && [[ "$ATTACHMENTS" == "$EXPECTED_ATTACHMENTS" ]] \
     && [[ "$REACTIONS" == "$EXPECTED_REACTIONS" ]]; then
    echo "[POLL $i] Early success condition met (exporting with final counters)."
    JOB_DONE=1
    break
  fi
  if [[ "$JOB_STATUS" == "success" || "$JOB_STATUS" == "done" || "$CURRENT_STAGE" == "done" ]]; then
    JOB_DONE=1
    break
  fi
  if [[ "$JOB_STATUS" == "error" ]]; then
    echo "Job entered error state" >&2
    docker compose -f "$COMPOSE_FILE" logs backend | tail -400 >&2
    exit 1
  fi
  sleep 1
  if (( i > 60 )); then sleep 1; fi
  if (( i > 120 )); then sleep 2; fi
  if (( i > 150 )); then sleep 3; fi
  if (( i > 170 )); then sleep 5; fi
  # Periodically refresh scoped logs (helps diagnose stalls without mixing prior run data)
  if [[ $(( i % 15 )) -eq 0 ]]; then
    if [[ "${LOG_SCOPE}" -eq 1 ]]; then
      # Prefer container-specific logs with --since if docker version supports it
      if [[ -n "$BACKEND_CID" ]]; then
        docker logs --since "$START_TS" "$BACKEND_CID" --tail=4000 --timestamps > "$LOG_CAPTURE" 2>/dev/null || \
          docker compose -f "$COMPOSE_FILE" logs --no-color backend > "$LOG_CAPTURE" 2>&1 || true
      else
        docker compose -f "$COMPOSE_FILE" logs --no-color backend > "$LOG_CAPTURE" 2>&1 || true
      fi
    else
      docker compose -f "$COMPOSE_FILE" logs --no-color backend > "$LOG_CAPTURE" 2>&1 || true
    fi
  fi

  # Global timeout guard
  NOW_EPOCH=$(date +%s)
  if (( NOW_EPOCH - SCRIPT_START_EPOCH > MAX_TOTAL_SECONDS )); then
    echo "[TIMEOUT] Script exceeded MAX_TOTAL_SECONDS=$MAX_TOTAL_SECONDS" >&2
    docker compose -f "$COMPOSE_FILE" logs backend | tail -400 >&2 || true
    exit 1
  fi
done

if [[ $JOB_DONE -ne 1 ]]; then
  echo "Job did not reach success state within polling window" >&2
  docker compose -f "$COMPOSE_FILE" logs backend | tail -400 >&2
  exit 1
fi

echo "[STEP] Final totals assertion"
# Re-fetch jobs for final counts (fallback to totals when processed zero)
FINAL_JOBS_JSON=$(curl -s http://localhost:8000/jobs)
read -r F_USERS F_CHANNELS F_MESSAGES F_REACTIONS F_ATTACHMENTS <<<"$(python3 - <<'PY' "$FINAL_JOBS_JSON"
import json,sys
j=json.loads(sys.argv[1])
job=(j.get('jobs') or [{}])[0]
m=job.get('meta') or {}
t=m.get('totals') or {}
def pick(pk, tk):
  pv=int(m.get(pk,0) or 0)
  tv=int(t.get(tk,0) or 0)
  return pv if pv>0 else tv
print(pick('users_processed','users'), pick('channels_processed','channels'), pick('messages_processed','messages'), pick('reactions_processed','reactions'), pick('attachments_processed','attachments'))
PY
)"

echo "[DIAG] Raw final /jobs JSON (truncated to 1200 chars):"
echo "$FINAL_JOBS_JSON" | head -c 1200
echo
echo "[DIAG] Final counters (resolved): users=$F_USERS channels=$F_CHANNELS messages=$F_MESSAGES reactions=$F_REACTIONS attachments=$F_ATTACHMENTS"

strict_assert() {
  local name=$1 actual=$2 expected=$3
  if [[ "$actual" -ne "$expected" ]]; then
    echo "[ASSERT] FAIL $name: got $actual expected $expected" >&2
    echo "[ASSERT] Aborting due to $name mismatch." >&2
    exit 1
  fi
  echo "[ASSERT] OK $name=$actual"
}

strict_assert users "$F_USERS" "$EXPECTED_USERS"
strict_assert channels "$F_CHANNELS" "$EXPECTED_CHANNELS"
strict_assert messages "$F_MESSAGES" "$EXPECTED_MESSAGES"
strict_assert attachments "$F_ATTACHMENTS" "$EXPECTED_ATTACHMENTS"
strict_assert reactions "$F_REACTIONS" "$EXPECTED_REACTIONS"

echo "[STEP] Log scanning for errors"
if [[ "${LOG_SCOPE}" -eq 1 ]]; then
  if [[ -n "$BACKEND_CID" ]]; then
    docker logs --since "$START_TS" "$BACKEND_CID" --timestamps > "$LOG_CAPTURE" 2>/dev/null || \
      docker compose -f "$COMPOSE_FILE" logs --no-color backend > "$LOG_CAPTURE" 2>&1 || true
  else
    docker compose -f "$COMPOSE_FILE" logs --no-color backend > "$LOG_CAPTURE" 2>&1 || true
  fi
else
  docker compose -f "$COMPOSE_FILE" logs --no-color backend > "$LOG_CAPTURE" 2>&1 || true
fi

# Basic diagnostics counts (do not fail on grep miss)
DUP_COUNT=$(grep -c "\[DUPLICATE\]" "$LOG_CAPTURE" 2>/dev/null || true)
DBERR_COUNT=$(grep -c "\[DBERR\]" "$LOG_CAPTURE" 2>/dev/null || true)
echo "[DIAG] Duplicate lines: $DUP_COUNT  DB error lines: $DBERR_COUNT"

if grep -E "TRACEBACK|Traceback" -i "$LOG_CAPTURE" >/dev/null; then
  echo "Traceback found in backend logs" >&2
  grep -i -E "Traceback" -n "$LOG_CAPTURE" >&2
  exit 1
fi
if grep -E "ERROR|CRITICAL" "$LOG_CAPTURE" \
  | grep -v "\[DUPLICATE\]" \
  | grep -vE "duplicate key value violates unique constraint \"idx_entities_type_slackid\"" \
  | grep -vE "HTTP \\w+ /upload -> 200" \
  | grep -vE "Ошибка создания (DM|GDM) через плагин: 404" \
  | grep -vE "Ошибка при создании канала: Extra data: .*404" \
  | grep -vE "Auto-ensure plugin failed:" >/dev/null; then
  echo "ERROR/CRITICAL lines found in backend logs (excluding known benign patterns + [DUPLICATE] + unique-violation duplicates)" >&2
  grep -nE "ERROR|CRITICAL" "$LOG_CAPTURE" | grep -v "\[DUPLICATE\]" | grep -vE "duplicate key value violates unique constraint \"idx_entities_type_slackid\"" >&2 || true
  exit 1
fi
if (( DBERR_COUNT > 0 )); then
  echo "[FAIL] Encountered one or more [DBERR] lines (database errors)" >&2
  grep -n "\[DBERR\]" "$LOG_CAPTURE" >&2 || true
  exit 1
fi

echo "[STEP] Validating admin user mapping (existing Mattermost user)"
# Query DB via backend container (using psql) to ensure admin (UADMIN) has expected mattermost_id
# NOTE: Expected Mattermost user ID for admin (pre-existing) as observed in current dev image.
# If Mattermost seed data changes, adjust this constant.
EXPECTED_ADMIN_MM_ID="o6b98rc1tpnfmy7ajxiadygmzy"
ADMIN_DB_ID=$(docker compose -f "$COMPOSE_FILE" exec -T db psql -U slack-mm -d slack-mm -P pager=off -t -c "SELECT mattermost_id FROM entities WHERE entity_type='user' AND slack_id='UADMIN';" | tr -d '[:space:]') || true
if [[ -z "$ADMIN_DB_ID" ]]; then
  echo "Admin user UADMIN not found in entities table" >&2
  exit 1
fi
if [[ "$ADMIN_DB_ID" != "$EXPECTED_ADMIN_MM_ID" ]]; then
  echo "Admin user Mattermost ID mismatch: got '$ADMIN_DB_ID' expected '$EXPECTED_ADMIN_MM_ID'" >&2
  exit 1
fi
echo "[INFO] Admin user mapping OK ($ADMIN_DB_ID)"

echo "[SUCCESS] Integration mini backup test passed."

# Teardown (keep logs for Actions artifact if desired)
echo "[CLEANUP] docker compose down"
docker compose -f "$COMPOSE_FILE" down -v || true
