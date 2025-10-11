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
# Expected counts (derived from mini dataset):
#   users=4 (2 real + 1 bot + 1 pre-existing admin user expected to map to existing Mattermost ID)
#   channels=3 (public, private, DM)
#   messages=13 (including thread replies, edited, deleted tombstone not counted as active message?)
#   attachments=3 (text file, image, zip)
#   reactions=1

# Expected counts (allow override via env for flexibility)
: "${EXPECTED_USERS:=4}"
: "${EXPECTED_CHANNELS:=3}"
: "${EXPECTED_MESSAGES:=13}"
: "${EXPECTED_ATTACHMENTS:=3}"
: "${EXPECTED_REACTIONS:=1}"

# Compose file, services list, dataset, and log capture path (override allowed)
: "${COMPOSE_FILE:=infra/docker-compose.dev.yml}"
SERVICES="${COMPOSE_SERVICES:-db mattermost backend}"
DATASET_FILE="${DATASET_FILE:-infra/test-data/slack-mini-backup.zip}"
LOG_CAPTURE=${LOG_CAPTURE:-/tmp/backend_integration_logs.txt}

echo "[INFO] Using compose file: $COMPOSE_FILE"
echo "[INFO] Services: $SERVICES"
echo "[INFO] Dataset file: $DATASET_FILE"

if [[ ! -f "$DATASET_FILE" ]]; then
  echo "Dataset file not found: $DATASET_FILE" >&2
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
    echo "[WAIT] plugin ensure attempt=$i code=$RESP status=$STATUS enabled=$ENABLED body=$(echo "$BODY" | head -c 140)" >&2
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
UPLOAD_RESP=$(curl -s -S -w '%{http_code}' -o /tmp/upload_resp.json -F "file=@${DATASET_FILE}" http://localhost:8000/upload || true)
if [[ "$UPLOAD_RESP" != "200" ]]; then
  echo "Upload failed (HTTP $UPLOAD_RESP)" >&2
  cat /tmp/upload_resp.json >&2 || true
  exit 1
fi
echo "[INFO] Upload succeeded"

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
  docker compose -f "$COMPOSE_FILE" logs --no-color backend > "$LOG_CAPTURE" 2>&1 || true
done

if [[ $JOB_DONE -ne 1 ]]; then
  echo "Job did not reach success state within polling window" >&2
  docker compose -f "$COMPOSE_FILE" logs backend | tail -400 >&2
  exit 1
fi

echo "[STEP] Final totals assertion"
# Re-fetch jobs for final counts
FINAL_JOBS_JSON=$(curl -s http://localhost:8000/jobs)
read -r F_USERS F_CHANNELS F_MESSAGES F_REACTIONS F_ATTACHMENTS <<<"$(python3 - <<'PY' "$FINAL_JOBS_JSON"
import json,sys
j=json.loads(sys.argv[1])
job=(j.get('jobs') or [{}])[0]
meta=job.get('meta') or {}
print(meta.get('users_processed',0), meta.get('channels_processed',0), meta.get('messages_processed',0), meta.get('reactions_processed',0), meta.get('attachments_processed',0))
PY
)"

if [[ "$F_USERS" -ne $EXPECTED_USERS ]]; then echo "Users mismatch: got $F_USERS expected $EXPECTED_USERS" >&2; exit 1; fi
if [[ "$F_CHANNELS" -ne $EXPECTED_CHANNELS ]]; then echo "Channels mismatch: got $F_CHANNELS expected $EXPECTED_CHANNELS" >&2; exit 1; fi
if [[ "$F_MESSAGES" -ne $EXPECTED_MESSAGES ]]; then echo "Messages mismatch: got $F_MESSAGES expected $EXPECTED_MESSAGES" >&2; exit 1; fi
if [[ "$F_ATTACHMENTS" -ne $EXPECTED_ATTACHMENTS ]]; then echo "Attachments mismatch: got $F_ATTACHMENTS expected $EXPECTED_ATTACHMENTS" >&2; exit 1; fi
if [[ "$F_REACTIONS" -ne $EXPECTED_REACTIONS ]]; then echo "Reactions mismatch: got $F_REACTIONS expected $EXPECTED_REACTIONS" >&2; exit 1; fi

echo "[STEP] Log scanning for errors"
docker compose -f "$COMPOSE_FILE" logs --no-color backend > "$LOG_CAPTURE" 2>&1 || true
if grep -E "TRACEBACK|Traceback" -i "$LOG_CAPTURE" >/dev/null; then
  echo "Traceback found in backend logs" >&2
  grep -i -E "Traceback" -n "$LOG_CAPTURE" >&2
  exit 1
fi
if grep -E "ERROR" "$LOG_CAPTURE" \
  | grep -vE "HTTP \w+ /upload -> 200" \
  | grep -vE "Ошибка создания (DM|GDM) через плагин: 404" \
  | grep -vE "Ошибка при создании канала: Extra data: .*404" \
  | grep -vE "Auto-ensure plugin failed:" >/dev/null; then
  echo "ERROR lines found in backend logs (excluding benign upload access log)" >&2
  grep -n "ERROR" "$LOG_CAPTURE" >&2
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
