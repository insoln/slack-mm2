# Mark-as-Read Implementation Flow

## High-Level Flow Diagram

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
│  1. Get all channel members (paginated, 200 per page)          │
│  2. For each member:                                             │
│     - Call updateMemberLastViewedAt(channelID, userID, ts)      │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│          updateMemberLastViewedAt(channelID, userID, timestamp)  │
│  1. Get current ChannelMember via p.API.GetChannelMember()     │
│  2. Check: timestamp > member.LastViewedAt?                      │
│  3. If yes, call updateLastViewedAtDirectly()                   │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│        updateLastViewedAtDirectly(channelID, userID, timestamp)  │
│  1. Get database connection via p.Driver.Conn()                 │
│  2. Execute SQL UPDATE:                                          │
│     UPDATE ChannelMembers                                        │
│     SET LastViewedAt = ?, MentionCount = 0, MentionCountRoot = 0│
│     WHERE ChannelId = ? AND UserId = ?                          │
│  3. Close connection                                             │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                    ┌──────────────────┐
                    │  Success/Warning │
                    │  Logged          │
                    └──────────────────┘
```

## Data Flow

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
       │                     │  GetChannelMembers() │
       │                     ├─────────────────────>│
       │                     │                      │
       │                     │<─────────────────────┤
       │                     │  Member list         │
       │                     │                      │
       │                     │  SQL UPDATE via      │
       │                     │  Driver.ConnExec()   │
       │                     ├─────────────────────>│
       │                     │  (For each member)   │
       │                     │                      │
       │                     │<─────────────────────┤
       │                     │  Update confirmed    │
       │                     │                      │
       │  <post_id>          │                      │
       │<────────────────────┤                      │
       │                     │                      │
```

## Database Schema Impact

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

## Error Handling Strategy

```
┌───────────────────────────────────────────────────────────────┐
│                     Error Scenarios                            │
├───────────────────────────────────────────────────────────────┤
│                                                                │
│  1. Post Creation Fails          → Return error immediately   │
│                                     (No mark-as-read attempt) │
│                                                                │
│  2. GetChannelMembers Fails      → Log warning, continue      │
│                                     (Post still created)       │
│                                                                │
│  3. Individual Member Update     → Log warning, skip member   │
│     Fails                           Continue with others       │
│                                                                │
│  4. Driver Not Available         → Log debug, skip update     │
│                                     (Graceful degradation)    │
│                                                                │
│  5. Database Connection Fails    → Log error, skip update     │
│                                     (Post remains created)     │
│                                                                │
└───────────────────────────────────────────────────────────────┘
```

## Performance Characteristics

```
Channel Size        Members    Operations     Estimated Time
───────────────────────────────────────────────────────────────
Small               < 50       1 page         ~100-200ms
Medium              50-200     1 page         ~200-500ms
Large               200-1000   2-5 pages      ~500ms-2s
Very Large          > 1000     6+ pages       ~2-5s

Notes:
- Each member requires: 1 GetChannelMember + 1 SQL UPDATE
- Pagination prevents memory issues with large channels
- Updates are sequential to maintain consistency
- Non-blocking: import completes regardless of mark-as-read time
```

## Configuration & Environment

```
┌───────────────────────────────────────────────────────────────┐
│              No Configuration Required                         │
├───────────────────────────────────────────────────────────────┤
│                                                                │
│  ✓ Feature is always enabled                                  │
│  ✓ No environment variables needed                            │
│  ✓ No plugin settings to configure                            │
│  ✓ Automatically activated on plugin load                     │
│                                                                │
│  Implementation follows repository policy:                     │
│  "Do not use environment variables as feature flags"          │
│                                                                │
└───────────────────────────────────────────────────────────────┘
```

## Testing Flow

```
test-mark-as-read.sh:

1. Get member's LastViewedAt (before)  ───┐
                                           │
2. Import post via plugin API          ───┤
                                           │
3. Wait 2 seconds                      ───┤  Validation
                                           │
4. Get member's LastViewedAt (after)   ───┤
                                           │
5. Compare timestamps                  ───┤
   Assert: after >= post_timestamp        │
   Assert: MentionCount == 0           ───┘

6. Cleanup: Delete test post
```
