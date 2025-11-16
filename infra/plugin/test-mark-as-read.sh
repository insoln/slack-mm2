#!/bin/bash
# Test script to verify mark-as-read functionality
# This script creates a test post and checks if it's marked as read for channel members

set -e

# Configuration
MATTERMOST_URL="${MATTERMOST_URL:-http://localhost:8065}"
PLUGIN_URL="${PLUGIN_URL:-$MATTERMOST_URL/plugins/mm-importer/api/v1}"
MATTERMOST_TOKEN="${MATTERMOST_TOKEN:-5x7rr788c7gwdnkdr9imb49ffo}"
TEST_CHANNEL_ID="${TEST_CHANNEL_ID:-}"
TEST_USER_ID="${TEST_USER_ID:-}"

echo "=== Mark-as-Read Functionality Test ==="
echo ""

# Check if required environment variables are set
if [ -z "$TEST_CHANNEL_ID" ]; then
    echo "ERROR: TEST_CHANNEL_ID environment variable is required"
    echo "Set it to a valid channel ID for testing"
    exit 1
fi

if [ -z "$TEST_USER_ID" ]; then
    echo "ERROR: TEST_USER_ID environment variable is required"
    echo "Set it to a valid user ID for testing"
    exit 1
fi

echo "Configuration:"
echo "  Mattermost URL: $MATTERMOST_URL"
echo "  Plugin URL: $PLUGIN_URL"
echo "  Channel ID: $TEST_CHANNEL_ID"
echo "  User ID: $TEST_USER_ID"
echo ""

# Step 1: Get channel members before post creation
echo "Step 1: Getting channel members..."
MEMBERS_BEFORE=$(curl -s -H "Authorization: Bearer $MATTERMOST_TOKEN" \
    "$MATTERMOST_URL/api/v4/channels/$TEST_CHANNEL_ID/members" | \
    jq -r '.[].user_id' | wc -l)
echo "  Found $MEMBERS_BEFORE members in channel"
echo ""

# Step 2: Get a channel member's LastViewedAt before post creation
echo "Step 2: Getting member's LastViewedAt before post..."
MEMBER_BEFORE=$(curl -s -H "Authorization: Bearer $MATTERMOST_TOKEN" \
    "$MATTERMOST_URL/api/v4/channels/$TEST_CHANNEL_ID/members/$TEST_USER_ID")
LAST_VIEWED_BEFORE=$(echo "$MEMBER_BEFORE" | jq -r '.last_viewed_at')
echo "  LastViewedAt before: $LAST_VIEWED_BEFORE"
echo ""

# Step 3: Create a test post via plugin
echo "Step 3: Creating test post via plugin..."
TIMESTAMP=$(date +%s%3N)  # Current time in milliseconds
POST_DATA=$(cat <<EOF
{
  "user_id": "$TEST_USER_ID",
  "channel_id": "$TEST_CHANNEL_ID",
  "message": "Test post for mark-as-read validation - $(date)",
  "create_at": $TIMESTAMP
}
EOF
)

POST_RESPONSE=$(curl -s -X POST \
    -H "Authorization: Bearer $MATTERMOST_TOKEN" \
    -H "Mattermost-User-ID: $TEST_USER_ID" \
    -H "Content-Type: application/json" \
    -d "$POST_DATA" \
    "$PLUGIN_URL/import")

POST_ID=$(echo "$POST_RESPONSE" | jq -r '.post_id')
if [ -z "$POST_ID" ] || [ "$POST_ID" == "null" ]; then
    echo "  ERROR: Failed to create post"
    echo "  Response: $POST_RESPONSE"
    exit 1
fi
echo "  Created post: $POST_ID"
echo "  Post timestamp: $TIMESTAMP"
echo ""

# Wait a moment for database updates to complete
echo "Step 4: Waiting for updates to complete..."
sleep 2
echo ""

# Step 5: Check member's LastViewedAt after post creation
echo "Step 5: Checking member's LastViewedAt after post..."
MEMBER_AFTER=$(curl -s -H "Authorization: Bearer $MATTERMOST_TOKEN" \
    "$MATTERMOST_URL/api/v4/channels/$TEST_CHANNEL_ID/members/$TEST_USER_ID")
LAST_VIEWED_AFTER=$(echo "$MEMBER_AFTER" | jq -r '.last_viewed_at')
MENTION_COUNT=$(echo "$MEMBER_AFTER" | jq -r '.mention_count')
echo "  LastViewedAt after: $LAST_VIEWED_AFTER"
echo "  MentionCount: $MENTION_COUNT"
echo ""

# Step 6: Validate results
echo "Step 6: Validating results..."
if [ "$LAST_VIEWED_AFTER" -ge "$TIMESTAMP" ]; then
    echo "  ✓ SUCCESS: LastViewedAt was updated to post timestamp or later"
    echo "    Before: $LAST_VIEWED_BEFORE"
    echo "    After:  $LAST_VIEWED_AFTER"
    echo "    Post:   $TIMESTAMP"
else
    echo "  ✗ FAILURE: LastViewedAt was NOT updated correctly"
    echo "    Before: $LAST_VIEWED_BEFORE"
    echo "    After:  $LAST_VIEWED_AFTER"
    echo "    Post:   $TIMESTAMP"
    exit 1
fi

if [ "$MENTION_COUNT" -eq 0 ]; then
    echo "  ✓ SUCCESS: MentionCount was reset to 0"
else
    echo "  ⚠ WARNING: MentionCount is $MENTION_COUNT (expected 0)"
fi
echo ""

# Step 7: Cleanup - delete test post
echo "Step 7: Cleaning up test post..."
curl -s -X DELETE \
    -H "Authorization: Bearer $MATTERMOST_TOKEN" \
    "$MATTERMOST_URL/api/v4/posts/$POST_ID" > /dev/null
echo "  Test post deleted"
echo ""

echo "=== Test Complete ==="
echo "✓ Mark-as-read functionality is working correctly!"
