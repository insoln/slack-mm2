# Mark Imported Posts as Read

## Overview

This document describes the implementation of the "mark as read" functionality for imported posts in the Mattermost plugin. This feature ensures that posts created during bulk import/synchronization operations are automatically marked as read for all channel members, preventing false notifications.

## Problem Statement

During synchronization, messages imported or created in Mattermost through the plugin appear in the list of new/unread messages. This leads to:
- False notifications for users
- Confusion during mass data import
- Large numbers of accumulated "unread" messages that were actually imported from historical data

## Solution

The implementation updates the `LastViewedAt` timestamp in the `ChannelMembers` table for all members of a channel whenever a post is imported. This timestamp determines which posts are considered "read" by each user.

## Implementation Details

### Key Functions

#### 1. `markPostAsReadForChannelMembers(channelID string, postCreateAt int64)`
- Called after successful post creation in `ImportPost` handler
- Retrieves all channel members using pagination (200 members per page)
- Calls `updateMemberLastViewedAt` for each member
- Non-fatal: logs warnings but doesn't fail the import if marking fails

#### 2. `updateMemberLastViewedAt(channelID, userID string, timestamp int64)`
- Gets current channel member state
- Only updates if new timestamp is greater than current `LastViewedAt`
- This prevents accidentally marking newer posts as unread
- Delegates to `updateLastViewedAtDirectly` for database update

#### 3. `updateLastViewedAtDirectly(channelID, userID string, timestamp int64)`
- Uses Mattermost's `Driver` interface for direct database access
- Executes SQL UPDATE query to set:
  - `LastViewedAt` = post creation timestamp
  - `MentionCount` = 0 (since post is marked read)
  - `MentionCountRoot` = 0 (for threaded replies)
- Works with both PostgreSQL and MySQL databases

#### 4. `makeDriverArgs(values ...interface{})`
- Helper function to convert Go values to `driver.NamedValue` slice
- Required by Mattermost's Driver interface for SQL query parameters

### SQL Query

```sql
UPDATE ChannelMembers 
SET LastViewedAt = ?, 
    MentionCount = 0,
    MentionCountRoot = 0
WHERE ChannelId = ? AND UserId = ?
```

## Why Direct Database Access?

The Mattermost plugin API does not provide a method to update the `LastViewedAt` field programmatically. According to Mattermost documentation, direct database updates are the recommended approach for bulk import/migration scenarios.

Alternative approaches considered:
1. ❌ REST API - No endpoint exists to mark posts as read
2. ❌ UpdateChannelMember API - This method doesn't exist in the plugin API
3. ✅ Direct database access via Driver - Recommended by Mattermost for bulk operations

## Testing

- Unit test added for `makeDriverArgs` helper function
- All existing tests continue to pass
- Server builds successfully
- Code formatted with `go fmt` and passes `go vet`
- Security scan (CodeQL) passes with no alerts

## Usage

The functionality is automatically invoked when importing posts via the `/api/v1/import` endpoint:

```bash
POST /plugins/mm-importer/api/v1/import
{
  "user_id": "user123",
  "channel_id": "channel456",
  "message": "Hello world",
  "create_at": 1637000000000
}
```

After the post is created, all channel members will have their `LastViewedAt` updated to the post's creation timestamp.

## Error Handling

- Database connection failures are logged but don't fail the import
- Member retrieval failures are logged and skipped
- Individual member update failures are logged, other members continue
- This ensures bulk imports complete even if some updates fail

## Performance Considerations

- Uses pagination when retrieving channel members (200 per page)
- Updates are performed sequentially per member to avoid race conditions
- For very large channels (1000+ members), this may take a few seconds
- Consider adding batch SQL updates in future for better performance

## Future Improvements

1. **Batch Updates**: Use a single SQL query to update all members at once
2. **Async Processing**: Move updates to background job for large channels
3. **Configuration**: Add option to disable this feature if not needed
4. **Metrics**: Track update success/failure rates
5. **Upstream Contribution**: Propose new plugin API method to Mattermost

## References

- [Mattermost Plugin API Documentation](https://developers.mattermost.com/integrate/reference/server/server-reference/)
- [Mattermost Driver Interface](https://pkg.go.dev/github.com/mattermost/mattermost/server/public/plugin#Driver)
- [ChannelMembers Database Schema](https://docs.mattermost.com/install/install-rhel-8-postgresql.html#database-schema)
