# Manual Testing Guide for Bot Export

This guide explains how to manually verify that Slack bots are correctly exported as Mattermost Bot Accounts.

## Prerequisites

- Docker and Docker Compose installed
- Slack export file containing at least one bot user

## Step 1: Prepare Test Data

Create a minimal Slack export with a bot user. You can use the example from the GitHub issue:

```bash
mkdir -p /tmp/slack-bot-test
cd /tmp/slack-bot-test

# Create users.json with a bot
cat > users.json << 'EOF'
[
  {
    "id": "U03AQJVH2HM",
    "name": "reminder_bot",
    "is_bot": true,
    "profile": {
      "real_name": "Reminder Bot",
      "first_name": "Reminder",
      "last_name": "Bot",
      "title": "A test reminder bot"
    }
  },
  {
    "id": "UREGULAR",
    "name": "john_doe",
    "is_bot": false,
    "profile": {
      "email": "john@example.com",
      "first_name": "John",
      "last_name": "Doe",
      "real_name": "John Doe"
    }
  }
]
EOF

# Create channels.json (required for import)
echo '[]' > channels.json

# Create a zip file
zip -r slack-export.zip users.json channels.json
```

## Step 2: Start Development Environment

```bash
cd /path/to/slack-mm2/infra

# Create .env.dev if it doesn't exist
cat > .env.dev << 'EOF'
SLACK_VERIFICATION_TOKEN=test_token
SLACK_BOT_TOKEN=test_bot_token
SLACK_SIGNING_SECRET=test_signing_secret
EOF

# Start all services
docker compose -f docker-compose.dev.yml up --build
```

**Important**: First build takes 15-30 minutes. Subsequent builds are faster.

Wait until you see:
- Backend: "Application startup complete"
- Mattermost: "Server is listening on :8065"

## Step 3: Upload and Process Slack Export

### Option A: Using the Web Interface

1. Open http://localhost:5173 in your browser
2. Upload the `slack-export.zip` file
3. Wait for processing to complete
4. Click "Start Export" to export to Mattermost

### Option B: Using curl

```bash
# Upload the export
curl -X POST http://localhost:8000/upload \
  -F "file=@/tmp/slack-bot-test/slack-export.zip"

# Check status
curl http://localhost:8000/stats

# Start export to Mattermost
curl -X POST http://localhost:8000/export/start
```

## Step 4: Verify Bots in Mattermost

### Via Web Interface

1. Open http://localhost:8065
2. Login with admin credentials:
   - Username: `admin`
   - Password: `P@ssw0rd`
3. Go to **System Console** → **Integrations** → **Bot Accounts**
4. You should see `reminder_bot` listed there
5. Click on the bot to see details
6. Verify:
   - Display Name: "Reminder Bot"
   - Username: "reminder_bot"

### Via API

```bash
# Get admin token (already set in dev environment)
TOKEN="5x7rr788c7gwdnkdr9imb49ffo"

# List all bots
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8065/api/v4/bots

# Expected output should include:
# {
#   "user_id": "...",
#   "username": "reminder_bot",
#   "display_name": "Reminder Bot",
#   "create_at": ...,
#   ...
# }

# Verify the bot user has is_bot=true
BOT_USER_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8065/api/v4/bots | jq -r '.[0].user_id')

curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8065/api/v4/users/$BOT_USER_ID

# Should show "is_bot": true in the response
```

## Step 5: Verify Regular Users Are Still Created Correctly

```bash
# List all users
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8065/api/v4/users?per_page=100

# Find john_doe user
# Should have "is_bot": false
# Should be in the users list, not in bots list
```

## Step 6: Check Database

```bash
# Connect to the database
docker compose -f docker-compose.dev.yml exec db \
  psql -U slack-mm -d slack-mm

# Check entities table
SELECT 
  slack_id, 
  mattermost_id, 
  status,
  raw_data->>'name' as username,
  raw_data->>'is_bot' as is_bot
FROM entities 
WHERE entity_type = 'user';

# Expected results:
# - reminder_bot: is_bot='true', status='success', has mattermost_id
# - john_doe: is_bot='false', status='success', has mattermost_id

# Exit psql
\q
```

## Expected Results

✓ **reminder_bot**:
- Appears in Mattermost Bot Accounts list
- API shows `is_bot: true`
- Database shows successful mapping
- Can be found via `/api/v4/bots` endpoint

✓ **john_doe**:
- Appears in regular Users list
- API shows `is_bot: false`
- Database shows successful mapping
- NOT in the bots list

## Troubleshooting

### Bot not appearing in Bot Accounts

Check backend logs:
```bash
docker compose -f docker-compose.dev.yml logs backend | grep -i "бот\|bot"
```

Look for:
- "Бот ... создан в Mattermost как Bot Account"
- Any error messages related to bot creation

### Bot creation fails with 403/401

Verify the Mattermost token has bot creation permissions:
```bash
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8065/api/v4/users/me
```

### Database shows bot but Mattermost doesn't

This feature only affects new imports. If a bot was previously imported as a regular user, it will remain a regular user. The fix applies to new imports going forward.

## Cleanup

```bash
# Stop all services
docker compose -f docker-compose.dev.yml down

# Remove volumes (resets all data)
docker compose -f docker-compose.dev.yml down -v
```
