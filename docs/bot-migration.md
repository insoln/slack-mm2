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

## Mattermost Configuration: EnableBotAccountCreation

The system automatically checks Mattermost's `EnableBotAccountCreation` configuration setting before attempting to create Bot Accounts. This ensures smooth imports regardless of the Mattermost server configuration.

### Behavior Based on Configuration:

1. **Bot creation enabled** (`EnableBotAccountCreation: true` - default):
   - Slack bots are exported as Mattermost Bot Accounts via `/api/v4/bots`
   - This is the recommended configuration for proper bot handling

2. **Bot creation disabled** (`EnableBotAccountCreation: false`):
   - Slack bots are automatically exported as regular users via `/api/v4/users`
   - A warning is logged for each bot exported as a user
   - Import continues successfully without manual intervention

3. **Config check failure** (network error, permission issue, etc.):
   - Assumes bot creation is enabled (fail-open approach)
   - Attempts to create as Bot Account
   - If creation fails, the error is logged

### Configuration Caching:

- The Mattermost config is retrieved once at the start of the export session
- Result is cached in memory to avoid repeated API calls (class-level cache)
- Cache persists for the lifetime of the export process

### Logging Examples:

```
INFO: Mattermost EnableBotAccountCreation: true
INFO: Бот B0001 создан в Mattermost как Bot Account с user_id: xyz123
```

```
INFO: Mattermost EnableBotAccountCreation: false
WARNING: Bot creation disabled in Mattermost config, exporting bot B0001 as regular user
```

### Enabling Bot Creation in Mattermost:

If you want bots to be created as Bot Accounts, ensure this setting is enabled:

1. **Via System Console**:
   - Navigate to **System Console → Integrations → Bot Accounts**
   - Enable **Bot Account Creation**

2. **Via config.json**:
   ```json
   {
     "ServiceSettings": {
       "EnableBotAccountCreation": true
     }
   }
   ```

## Handling Existing Data

**Important:** This feature only affects **new imports**. Existing bots that were already imported as regular users will remain as regular users in the database and Mattermost.

If you need to convert existing bots:
1. The old bot user accounts will remain as regular users in Mattermost
2. New imports will correctly create bots as Bot Accounts
3. You can manually deactivate old bot user accounts in Mattermost if desired (System Console → Users)

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

2. **Bot Listing Pagination**: Mattermost doesn't provide a direct bot lookup by username API. The current implementation lists up to 200 bots and searches for matches. For deployments with >200 bots:
   - Existing bots may not be found (will create duplicates)
   - Consider implementing pagination if needed
   - Most deployments have <200 bots, so this is typically sufficient

3. **Avatar Upload**: Avatar upload for bots works the same as for users, using the same endpoint.

## References

- [Mattermost Bot API Documentation](https://api.mattermost.com/#tag/bots)
- [Mattermost User API Documentation](https://api.mattermost.com/#tag/users)
- GitHub Issue: "Боты из Slack создаются в Mattermost как обычные пользователи"
