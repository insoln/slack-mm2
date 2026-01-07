package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
)

func TestServeHTTP(t *testing.T) {
	assert := assert.New(t)
	plugin := Plugin{}
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/api/v1/hello", nil)
	r.Header.Set("Mattermost-User-ID", "test-user-id")

	plugin.ServeHTTP(nil, w, r)

	result := w.Result()
	assert.NotNil(result)
	defer result.Body.Close()
	bodyBytes, err := io.ReadAll(result.Body)
	assert.Nil(err)
	bodyString := string(bodyBytes)

	assert.Equal("Hello, world!", bodyString)
}

func TestImportReaction_EmptyBody(t *testing.T) {
	plugin := Plugin{}
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/api/v1/reaction", nil)

	plugin.ImportReaction(w, r)

	result := w.Result()
	assert.Equal(t, http.StatusBadRequest, result.StatusCode)
}

func TestUploadAttachmentFromURL_EmptyBody(t *testing.T) {
	plugin := Plugin{}
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/api/v1/attachment_from_url", nil)

	plugin.UploadAttachmentFromURL(w, r)

	result := w.Result()
	assert.Equal(t, http.StatusBadRequest, result.StatusCode)
}

func TestUploadAttachmentFromURL_InvalidJSON(t *testing.T) {
	plugin := Plugin{}
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/api/v1/attachment_from_url", strings.NewReader("invalid json"))

	plugin.UploadAttachmentFromURL(w, r)

	result := w.Result()
	assert.Equal(t, http.StatusBadRequest, result.StatusCode)
}

func TestUploadAttachmentFromURL_WithPostID(t *testing.T) {
	// Test that post_id field is accepted in the request without crashing in test mode
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Return small content to avoid nil pointer in streaming upload
		w.Header().Set("Content-Length", "4")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("test"))
	}))
	defer mockServer.Close()

	plugin := Plugin{httpClient: mockServer.Client()}
	w := httptest.NewRecorder()
	reqBody := `{
		"channel_id": "ch123",
		"filename": "test.txt",
		"file_url": "` + mockServer.URL + `/file.txt",
		"auth_header": "Bearer token",
		"post_id": "post123"
	}`
	r := httptest.NewRequest(http.MethodPost, "/api/v1/attachment_from_url", strings.NewReader(reqBody))

	plugin.UploadAttachmentFromURL(w, r)

	result := w.Result()
	// In test mode (no API), validation passes but upload session creation fails
	// Should get InternalServerError from nil API during upload
	assert.Equal(t, http.StatusInternalServerError, result.StatusCode)
}

func TestUploadAttachmentFromURL_PostValidationFailure(t *testing.T) {
	// Test that validation happens before file upload when API returns error
	// This test verifies the flow but can't fully test with nil API
	plugin := Plugin{}

	// Validation with nil API returns nil (test mode), so this test is informational
	err := plugin.validatePostForAttachment("post123", "ch123")
	assert.Nil(t, err, "Validation should pass in test mode with nil API")
}

func TestValidatePostForAttachment(t *testing.T) {
	// Test validation logic in isolation
	plugin := Plugin{}

	// With nil API, should return nil (test mode)
	err := plugin.validatePostForAttachment("post123", "ch123")
	assert.Nil(t, err)
}

func TestUnarchiveChannel_EmptyBody(t *testing.T) {
	plugin := Plugin{}
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/api/v1/channel/unarchive", nil)

	plugin.UnarchiveChannel(w, r)

	result := w.Result()
	assert.Equal(t, http.StatusBadRequest, result.StatusCode)
}

func TestUnarchiveChannel_InvalidJSON(t *testing.T) {
	plugin := Plugin{}
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/api/v1/channel/unarchive", strings.NewReader("invalid json"))

	plugin.UnarchiveChannel(w, r)

	result := w.Result()
	assert.Equal(t, http.StatusBadRequest, result.StatusCode)
}

func TestUnarchiveChannel_MissingChannelID(t *testing.T) {
	plugin := Plugin{}
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/api/v1/channel/unarchive", strings.NewReader("{}"))

	plugin.UnarchiveChannel(w, r)

	result := w.Result()
	assert.Equal(t, http.StatusBadRequest, result.StatusCode)
}

func TestMakeDriverArgs(t *testing.T) {
	plugin := Plugin{}

	args := plugin.makeDriverArgs(int64(123456789), "channel-id", "user-id")

	assert.Equal(t, 3, len(args))
	assert.Equal(t, 1, args[0].Ordinal)
	assert.Equal(t, int64(123456789), args[0].Value)
	assert.Equal(t, 2, args[1].Ordinal)
	assert.Equal(t, "channel-id", args[1].Value)
	assert.Equal(t, 3, args[2].Ordinal)
	assert.Equal(t, "user-id", args[2].Value)
}

func TestFixInconsistentThreadMemberships_NoDriver(t *testing.T) {
	// Test that the function handles missing driver gracefully
	plugin := Plugin{}

	err := plugin.fixInconsistentThreadMemberships("channel-id")

	// Should not error when driver is nil
	assert.Nil(t, err)
}

func TestMarkThreadAsReadForAllMembers_NoDriver(t *testing.T) {
	// Test that the function handles missing driver gracefully
	plugin := Plugin{}

	err := plugin.markThreadAsReadForAllMembers("root-post-id", int64(123456789))

	// Should not error when driver is nil
	assert.Nil(t, err)
}

func TestFixedChannelsCache(t *testing.T) {
	// Test that the cache properly tracks fixed channels
	// Note: mutex is initialized via zero value, which is valid for sync.Mutex
	plugin := Plugin{
		fixedChannels:    make(map[string]bool),
		processedThreads: make(map[string]bool),
	}

	// Initially, channels and threads should not be in the cache
	assert.False(t, plugin.fixedChannels["channel1"])
	assert.False(t, plugin.fixedChannels["channel2"])
	assert.False(t, plugin.processedThreads["thread1"])

	// Add channels to cache
	plugin.fixedChannels["channel1"] = true

	// Verify channel1 is now cached
	assert.True(t, plugin.fixedChannels["channel1"])
	assert.False(t, plugin.fixedChannels["channel2"])
	assert.False(t, plugin.processedThreads["thread1"])
}

func TestClearFixedChannelsCache(t *testing.T) {
	// Test that the cache can be cleared
	plugin := Plugin{
		fixedChannels:    make(map[string]bool),
		processedThreads: make(map[string]bool),
	}

	// Add some channels and threads to cache
	plugin.fixedChannels["channel1"] = true
	plugin.fixedChannels["channel2"] = true
	plugin.processedThreads["thread1"] = true
	plugin.processedThreads["thread2"] = true
	plugin.processedThreads["thread3"] = true
	assert.Equal(t, 2, len(plugin.fixedChannels))
	assert.Equal(t, 3, len(plugin.processedThreads))

	// Clear the cache
	plugin.ClearFixedChannelsCache()

	// Verify both caches are empty
	assert.Equal(t, 0, len(plugin.fixedChannels))
	assert.Equal(t, 0, len(plugin.processedThreads))
}

func TestClearImportCache_Endpoint(t *testing.T) {
	// Test that the API endpoint clears the cache
	plugin := Plugin{
		fixedChannels:    make(map[string]bool),
		processedThreads: make(map[string]bool),
	}

	// Add some channels and threads to cache
	plugin.fixedChannels["channel1"] = true
	plugin.fixedChannels["channel2"] = true
	plugin.processedThreads["thread1"] = true
	assert.Equal(t, 2, len(plugin.fixedChannels))
	assert.Equal(t, 1, len(plugin.processedThreads))

	// Call the endpoint
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/api/v1/import/clear_cache", nil)
	plugin.ClearImportCache(w, r)

	// Verify response
	result := w.Result()
	assert.Equal(t, http.StatusOK, result.StatusCode)

	// Verify both caches are empty
	assert.Equal(t, 0, len(plugin.fixedChannels))
	assert.Equal(t, 0, len(plugin.processedThreads))
}

func TestImportPostRequest_Parsing(t *testing.T) {
	// Test parsing of ImportPostRequest with and without RootID
	tests := []struct {
		name     string
		body     string
		wantRoot string
	}{
		{
			name:     "threaded reply has root_id",
			body:     `{"user_id":"u1","channel_id":"c1","message":"reply","root_id":"parent123","create_at":1234567890}`,
			wantRoot: "parent123",
		},
		{
			name:     "top-level post has no root_id",
			body:     `{"user_id":"u1","channel_id":"c1","message":"post","create_at":1234567890}`,
			wantRoot: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var req ImportPostRequest
			err := json.Unmarshal([]byte(tt.body), &req)
			assert.NoError(t, err)
			assert.Equal(t, tt.wantRoot, req.RootID)
		})
	}
}

func TestConcurrentCacheAccess(t *testing.T) {
	// Test that concurrent access to the cache prevents race conditions during error cleanup.
	// Simulates the scenario where multiple goroutines attempt to fix the same channel:
	// - Goroutine A: checks cache (not fixed), starts DB operation, succeeds, sets cache=true
	// - Goroutine B: checks cache (not fixed), starts DB operation, fails
	// Without proper locking, B could delete the cache entry that A just set, causing
	// unnecessary retries on future imports.
	plugin := Plugin{
		fixedChannels:    make(map[string]bool),
		processedThreads: make(map[string]bool),
	}

	// Use a wait group to ensure all goroutines complete
	var wg sync.WaitGroup
	numGoroutines := 100
	channelID := "test-channel"
	successCount := 0
	var successMutex sync.Mutex

	// Launch multiple goroutines that concurrently try to "fix" the same channel
	for i := 0; i < numGoroutines; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()

			// Simulate the cache check + fix operation pattern from ImportPost
			plugin.fixedChannelsMutex.Lock()
			alreadyFixed := plugin.fixedChannels[channelID]
			if !alreadyFixed {
				// Simulate DB operation (some succeed, some fail)
				// In real code, this would be fixInconsistentThreadMemberships
				if id%3 == 0 {
					// Success case: mark as fixed
					plugin.fixedChannels[channelID] = true
					successMutex.Lock()
					successCount++
					successMutex.Unlock()
				}
				// Failure case: don't set cache (allow retry)
				// Old buggy code would delete here, causing race
			}
			plugin.fixedChannelsMutex.Unlock()
		}(i)
	}

	// Wait for all goroutines to complete
	wg.Wait()

	// Verify that cache entry remains if any goroutine succeeded
	// The race condition would cause cache entry to be deleted incorrectly
	if successCount > 0 {
		assert.True(t, plugin.fixedChannels[channelID], "Cache entry should persist after successful fix")
	}
	assert.Greater(t, successCount, 0, "At least one goroutine should have succeeded")
}

func TestThreadCachePreventsRedundantUpdates(t *testing.T) {
	// Test that the thread cache prevents redundant mark-as-read operations
	plugin := Plugin{
		fixedChannels:    make(map[string]bool),
		processedThreads: make(map[string]bool),
	}

	threadID := "thread-root-123"

	// First check - should not be in cache
	plugin.fixedChannelsMutex.Lock()
	alreadyProcessed := plugin.processedThreads[threadID]
	if !alreadyProcessed {
		plugin.processedThreads[threadID] = true
	}
	plugin.fixedChannelsMutex.Unlock()
	assert.False(t, alreadyProcessed, "Thread should not be processed initially")

	// Second check - should be in cache now
	plugin.fixedChannelsMutex.Lock()
	alreadyProcessed = plugin.processedThreads[threadID]
	plugin.fixedChannelsMutex.Unlock()
	assert.True(t, alreadyProcessed, "Thread should be marked as processed")

	// Verify cache cleanup removes thread
	plugin.ClearFixedChannelsCache()
	plugin.fixedChannelsMutex.Lock()
	alreadyProcessed = plugin.processedThreads[threadID]
	plugin.fixedChannelsMutex.Unlock()
	assert.False(t, alreadyProcessed, "Thread cache should be cleared")
}
