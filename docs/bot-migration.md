# Slack Bot to Mattermost Bot Account Migration

## Overview

This document describes the implementation of proper Slack bot mapping to Mattermost Bot Accounts instead of regular user accounts.

## Problem

Previously, Slack bots (users with `is_bot=true`) were being created as regular User accounts in Mattermost instead of Bot Accounts. This caused several issues:

- Extra "users" consuming licenses
- Incorrect bot policies
- Unnecessary notifications
- Confusion between actual users and bots

## Solution

The system now detects Slack bots and creates them as Mattermost Bot Accounts using the appropriate API endpoint.

### Detection

Slack bots are identified by the `is_bot` field in their user data:

```json
{
  "id": "U03AQJVH2HM",
  "name": "reminder_bot",
  "is_bot": true,
  "profile": {
    "real_name": "Reminder Bot",
    "first_name": "Reminder",
    "last_name": "Bot"
  }
}
```

### Bot Creation

When a bot is detected:

1. The system checks if a bot with the same username already exists in Mattermost
2. If it exists, the existing bot's `user_id` is reused
3. If it doesn't exist, a new Bot Account is created using `POST /api/v4/bots`
4. The bot's `user_id` is stored in the `mattermost_id` field for consistency

### API Endpoints

**Regular Users**: `POST /api/v4/users`
```json
{
  "username": "john_doe",
  "email": "john@example.com",
  "password": "",
  "first_name": "John",
  "last_name": "Doe",
  "auth_service": "gitlab",
  "auth_data": "..."
}
```

**Bots**: `POST /api/v4/bots`
```json
{
  "username": "reminder_bot",
  "display_name": "Reminder Bot",
  "description": "A reminder bot"
}
```

Response from bot creation includes `user_id`:
```json
{
  "user_id": "abc123...",
  "username": "reminder_bot",
  "display_name": "Reminder Bot"
}
```

### Bot Reuse

To check if a bot already exists, the system:

1. Calls `GET /api/v4/bots?per_page=200`
2. Searches the returned list for a bot with matching username
3. If found, uses the existing bot's `user_id`

Note: Mattermost doesn't provide a direct bot lookup by username endpoint, so we need to list and search.

## Migration for Existing Data

If you have already imported Slack bots as regular users, you need to migrate them:

### Step 1: Run the Migration Script

```bash
cd /home/runner/work/slack-mm2/slack-mm2
source .venv/bin/activate
python backend/alembic/versions/004_mark_bots_for_reexport.py
```

This script:
- Identifies all bot entities in the database (where `is_bot=true`)
- Marks successfully exported bots as `pending` for re-export
- Sets an error message explaining why they need re-export

### Step 2: Re-export the Data

Run your normal export process. The system will:
- Re-create marked bots as Bot Accounts
- Update the mapping with the new bot `user_id`

### Step 3: Clean Up Old User Accounts

Manually in Mattermost:
1. Navigate to System Console → Users
2. Search for the old bot accounts (they will appear as regular users)
3. Deactivate or delete them

The new Bot Accounts will be separate entities with their own IDs.

## Testing

### Unit Tests

Tests cover:
- Bot detection (`_is_slack_bot()`)
- Bot payload generation (`_build_bot_payload()`)
- Bot creation via correct API endpoint
- Bot reuse when already exists
- Regular user creation still works correctly

Run tests:
```bash
cd backend
pytest tests/unit/test_user_exporter.py -v
```

### Integration Tests

Tests verify end-to-end bot export:
- Bot vs regular user export paths
- Real-world bot data from GitHub issue

Run tests:
```bash
cd backend
pytest tests/integration/test_bot_export.py -v
```

### Manual Verification on Development Environment

To verify bots are created correctly on a test environment:

1. Start the development environment:
   ```bash
   cd infra
   docker compose -f docker-compose.dev.yml up
   ```

2. Upload a Slack export containing bots

3. Run the export process

4. Verify in Mattermost:
   - Go to System Console → Bots
   - You should see the bots listed there
   - Check that they have `is_bot=true` in their user profile

5. Verify via API:
   ```bash
   curl -H "Authorization: Bearer YOUR_TOKEN" \
        http://localhost:8065/api/v4/bots
   ```

## Implementation Details

### Code Changes

**File**: `backend/app/services/export/user_exporter.py`

Key methods:
- `_is_slack_bot()`: Detects if entity is a bot
- `_build_bot_payload()`: Constructs bot creation payload
- `_find_existing_bot(username)`: Searches for existing bot
- `_export_as_bot()`: Handles bot export workflow
- `_export_as_user()`: Handles regular user export workflow
- `export_entity()`: Router that delegates to bot or user export

### Database Schema

No schema changes required. The `entities` table stores:
- `entity_type`: "user" (for both bots and regular users)
- `slack_id`: Slack user/bot ID
- `mattermost_id`: Mattermost user_id (for both regular users and bots)
- `raw_data`: Complete Slack user/bot data (includes `is_bot` field)
- `status`: pending/success/failed/skipped

Bot detection is done at export time by checking `raw_data.is_bot`.

## Limitations

1. **No User-to-Bot Conversion**: Mattermost API doesn't support converting a regular user to a bot. Migration requires re-creating the bot and manually cleaning up the old user account.

2. **Bot Listing Limitation**: Mattermost doesn't provide a direct bot lookup by username. We must list all bots and search, which may be slow if there are many bots (>200 requires pagination).

3. **Avatar Upload**: Avatar upload for bots works the same as for users, using the same endpoint.

## References

- [Mattermost Bot API Documentation](https://api.mattermost.com/#tag/bots)
- [Mattermost User API Documentation](https://api.mattermost.com/#tag/users)
- GitHub Issue: "Боты из Slack создаются в Mattermost как обычные пользователи"
