# Mark Imported Posts as Read

## Overview

This document describes the implementation of the "mark as read" functionality for imported posts in the Mattermost plugin. This feature ensures that posts created during bulk import/synchronization operations are automatically marked as read for all channel members, preventing false notifications.

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     Client/Backend Calls                         │
│                  POST /plugins/mm-importer/api/v1/import         │
└───────────────────────────────┬─────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ImportPost Handler                           │
│  1. Validate request (user_id, channel_id, message)            │
│  2. Create model.Post object with metadata                      │
│  3. Call p.API.CreatePost(post)                                 │
└───────────────────────────────┬─────────────────────────────────┘
            │
            ▼
          ┌──────────────────┐
          │  Post Created?   │
          └────┬────────┬────┘
          │ Yes    │ No
          ▼        │
    ┌───────────────────────┘
    │                        │
    │                        ▼
    │              ┌─────────────────┐
    │              │  Return Error   │
    │              └─────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│          markPostAsReadForChannelMembers(channelID, timestamp)   │
│  1. Ensure Driver is available                                 │
│  2. Open DB connection via p.Driver.Conn()                       │
│  3. Execute SQL UPDATE targeting all members in one statement    │
│     (WHERE ChannelId = ? AND LastViewedAt < ?)                   │
│  4. Reset mention counters + LastViewedAt                        │
│  5. Log rows affected / close connection                         │
└─────────────────────────────────────────────────────────────────┘
            │
            ▼
          ┌──────────────────┐
          │  Success/Warning │
          │  Logged          │
          └──────────────────┘
```

### Data Flow

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   Backend    │──────│    Plugin    │──────│  Mattermost  │
│  (Python)    │ HTTP │   (Go)       │ API  │   Server     │
└──────────────┘      └──────────────┘      └──────────────┘
  │                     │                      │
  │  POST /import       │                      │
  │  with post data     │                      │
  ├────────────────────>│                      │
  │                     │                      │
  │                     │  CreatePost()        │
  │                     ├─────────────────────>│
  │                     │                      │
  │                     │<─────────────────────┤
  │                     │  Post created        │
  │                     │                      │
      │                     │  SQL UPDATE via      │
      │                     │  Driver.ConnExec()   │
      │                     ├─────────────────────>│
      │                     │  (Single statement)  │
  │                     │                      │
      │                     │<─────────────────────┤
      │                     │  Rows affected       │
  │                     │                      │
  │  <post_id>          │                      │
  │<────────────────────┤                      │
  │                     │                      │
```

## Problem Statement

During synchronization, messages imported or created in Mattermost through the plugin appear in the list of new/unread messages. This leads to:
- False notifications for users
- Confusion during mass data import
- Large numbers of accumulated "unread" messages that were actually imported from historical data

## Solution

The implementation updates the `LastViewedAt` timestamp in the `ChannelMembers` table for all members of a channel whenever a post is imported. This timestamp determines which posts are considered "read" by each user.

## Implementation Details

### Key Functions

#### `markPostAsReadForChannelMembers(channelID string, postCreateAt int64)`
- Called after successful post creation in `ImportPost`
- Executes a **single** SQL `UPDATE` via the plugin `Driver` to touch every member row in one shot
- Updates only members whose `LastViewedAt` is older than the imported post timestamp, preserving concurrency safety
- Resets `MentionCount` / `MentionCountRoot` alongside `LastViewedAt`
- Logs the number of affected rows to aid diagnostics but never fails the request

#### `makeDriverArgs(values ...interface{})`
- Helper to convert Go values to a `[]driver.NamedValue`
- Required by Mattermost's Driver interface for SQL query parameters

### SQL Query

```sql
UPDATE ChannelMembers
SET LastViewedAt = ?,
  MentionCount = 0,
  MentionCountRoot = 0
WHERE ChannelId = ? AND LastViewedAt < ?
```

## Why Direct Database Access?

The Mattermost plugin API does not provide a method to update the `LastViewedAt` field programmatically. According to Mattermost documentation, direct database updates are the recommended approach for bulk import/migration scenarios.

Alternative approaches considered:
1. ❌ REST API - No endpoint exists to mark posts as read
2. ❌ UpdateChannelMember API - This method doesn't exist in the plugin API
3. ✅ Direct database access via Driver - Recommended by Mattermost for bulk operations

### Database Impact

```
┌──────────────────────────────────────────────────────────────┐
│                     ChannelMembers Table                       │
├──────────────────────────────────────────────────────────────┤
│ ChannelId     VARCHAR(26)    PK                              │
│ UserId        VARCHAR(26)    PK                              │
│ LastViewedAt  BIGINT         ← UPDATED by mark-as-read      │
│ MentionCount  INT            ← RESET to 0                    │
│ MentionCountRoot INT         ← RESET to 0                    │
│ ...                                                           │
└──────────────────────────────────────────────────────────────┘

Before Import:
  LastViewedAt:  1700000000000  (old timestamp)
  MentionCount:  5
  
After Import of post at 1700500000000:
  LastViewedAt:  1700500000000  (updated to post time)
  MentionCount:  0               (reset)
```

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

```
┌───────────────────────────────────────────────────────────────┐
│                     Error Scenarios                            │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  1. Post Creation Fails          → Return error immediately   │
│                                     (No mark-as-read attempt) │
│                                                               │
│  2. Driver Not Available         → Log debug, skip update     │
│                                     (Graceful degradation)    │
│                                                               │
│  3. Database Connection Fails    → Log error, skip update     │
│                                     (Post remains created)    │
│                                                               │
│  4. SQL UPDATE fails             → Log warning/error, but     │
│                                     import still succeeds     │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

- Database connection failures are logged but don't fail the import
- Member retrieval failures are logged and skipped
- Individual member update failures are logged, other members continue
- This ensures bulk imports complete even if some updates fail

## Performance Considerations

- Uses a single batch update regardless of channel size; runtime is proportional to the database engine's ability to update matching rows
- Eliminates the old N+1 pattern of `GetChannelMembers` + per-member updates, dramatically improving throughput on large channels
- Atomic WHERE clause ensures no race condition when concurrent imports target the same channel

## Future Improvements

1. **Batch Updates**: Use a single SQL query to update all members at once (already implemented; consider further optimizations such as partitioned scans)
2. **Async Processing**: Move updates to background job for very large channels
3. **Configuration**: Add option to disable this feature if not needed
4. **Metrics**: Track update success/failure rates
5. **Upstream Contribution**: Propose new plugin API method to Mattermost

## References

- [Mattermost Plugin API Documentation](https://developers.mattermost.com/integrate/reference/server/server-reference/)
- [Mattermost Driver Interface](https://pkg.go.dev/github.com/mattermost/mattermost/server/public/plugin#Driver)
- [ChannelMembers Database Schema](https://docs.mattermost.com/install/install-rhel-8-postgresql.html#database-schema)
