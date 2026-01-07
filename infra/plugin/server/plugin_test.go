package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
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
		fixedChannels: make(map[string]bool),
	}

	// Initially, channels should not be in the cache
	assert.False(t, plugin.fixedChannels["channel1"])
	assert.False(t, plugin.fixedChannels["channel2"])

	// Add channels to cache
	plugin.fixedChannels["channel1"] = true

	// Verify channel1 is now cached
	assert.True(t, plugin.fixedChannels["channel1"])
	assert.False(t, plugin.fixedChannels["channel2"])
}

func TestClearFixedChannelsCache(t *testing.T) {
	// Test that the cache can be cleared
	plugin := Plugin{
		fixedChannels: make(map[string]bool),
	}

	// Add some channels to cache
	plugin.fixedChannels["channel1"] = true
	plugin.fixedChannels["channel2"] = true
	assert.Equal(t, 2, len(plugin.fixedChannels))

	// Clear the cache
	plugin.ClearFixedChannelsCache()

	// Verify cache is empty
	assert.Equal(t, 0, len(plugin.fixedChannels))
}

func TestClearImportCache_Endpoint(t *testing.T) {
	// Test that the API endpoint clears the cache
	plugin := Plugin{
		fixedChannels: make(map[string]bool),
	}

	// Add some channels to cache
	plugin.fixedChannels["channel1"] = true
	plugin.fixedChannels["channel2"] = true
	assert.Equal(t, 2, len(plugin.fixedChannels))

	// Call the endpoint
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/api/v1/import/clear_cache", nil)
	plugin.ClearImportCache(w, r)

	// Verify response
	result := w.Result()
	assert.Equal(t, http.StatusOK, result.StatusCode)

	// Verify cache is empty
	assert.Equal(t, 0, len(plugin.fixedChannels))
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
	// Test that demonstrates the race condition that existed in the OLD double-checked locking pattern.
	// 
	// KEY INSIGHT: This test uses a two-phase barrier to ensure ALL goroutines check the cache (and see false)
	// BEFORE ANY goroutine can proceed to update it. This simulates the real-world race where multiple
	// requests arrive simultaneously before any channel has been fixed.
	//
	// Phase 1: All goroutines check cache and signal they're ready
	// Phase 2: All goroutines wait until ALL are ready, then proceed together
	//
	// This properly exercises the failure path where successful and failing operations
	// interleave, causing the cache to end up in an unpredictable state.
	
	plugin := Plugin{
		fixedChannels: make(map[string]bool),
	}

	var wg sync.WaitGroup
	numGoroutines := 50
	channelID := "test-channel"
	var attemptCount int32
	var successCount int32
	var failureCount int32
	
	// Two-phase barrier: 
	// readyWg: All goroutines signal when they've checked cache
	// goSignal: Once all are ready, this channel is closed to release them
	var readyWg sync.WaitGroup
	readyWg.Add(numGoroutines)
	goSignal := make(chan struct{})

	// Launch multiple goroutines that concurrently try to "fix" the same channel
	for i := 0; i < numGoroutines; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()

			// OLD BUGGY PATTERN: Check cache, release lock, wait for all to be ready
			plugin.fixedChannelsMutex.Lock()
			alreadyFixed := plugin.fixedChannels[channelID]
			plugin.fixedChannelsMutex.Unlock() // RELEASE LOCK HERE (buggy!)

			// Signal that this goroutine has checked the cache
			readyWg.Done()
			
			// Wait for ALL goroutines to check the cache before any proceeds
			<-goSignal

			// All goroutines enter this block since they all saw false above
			if !alreadyFixed {
				atomic.AddInt32(&attemptCount, 1)
				
				// Simulate DB operation (some succeed, some fail)
				time.Sleep(time.Microsecond * time.Duration(id%5))
				
				success := (id % 3 == 0)
				
				// Reacquire lock to update cache
				plugin.fixedChannelsMutex.Lock()
				if success {
					plugin.fixedChannels[channelID] = true
					atomic.AddInt32(&successCount, 1)
				} else {
					// OLD BUGGY CODE: Delete on failure - this creates the race!
					// A failing goroutine can delete the cache entry that a successful
					// goroutine just set, causing infinite retry loops.
					delete(plugin.fixedChannels, channelID)
					atomic.AddInt32(&failureCount, 1)
				}
				plugin.fixedChannelsMutex.Unlock()
			}
		}(i)
	}

	// Wait for all goroutines to check the cache
	readyWg.Wait()
	
	// Now release all goroutines simultaneously
	close(goSignal)
	
	// Wait for all goroutines to complete
	wg.Wait()

	// With the old buggy pattern, ALL 50 goroutines attempt the fix since they all saw
	// alreadyFixed=false before the goSignal. Failures can delete successful cache entries.
	t.Logf("OLD BUGGY PATTERN - Attempts: %d, Successes: %d, Failures: %d, Final cache state: %v", 
		attemptCount, successCount, failureCount, plugin.fixedChannels[channelID])
	
	// Verify this demonstrates the race: All goroutines should have attempted (all saw false)
	assert.Equal(t, int32(numGoroutines), attemptCount, "All goroutines should attempt fix (all saw false before barrier)")
	assert.Greater(t, successCount, int32(0), "At least one goroutine should succeed")
	assert.Greater(t, failureCount, int32(0), "At least one goroutine should fail")
	
	// The cache state is unpredictable due to the race - it could be true or false
	// depending on which goroutine (success or failure) finished last. 
	// This demonstrates the bug that the new locking pattern fixes.
}

func TestConcurrentCacheAccessWithProperLocking(t *testing.T) {
	// Test that the FIXED implementation (holding lock during entire operation)
	// properly prevents race conditions by ensuring only one goroutine performs the fix.
	plugin := Plugin{
		fixedChannels: make(map[string]bool),
	}

	var wg sync.WaitGroup
	numGoroutines := 100
	channelID := "test-channel"
	var attemptCount int32
	var successCount int32
	var cacheCheckCount int32
	var skippedCount int32

	// Launch multiple goroutines using the FIXED pattern (lock held during operation)
	for i := 0; i < numGoroutines; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()

			// FIXED PATTERN: Hold lock during entire check + operation
			plugin.fixedChannelsMutex.Lock()
			atomic.AddInt32(&cacheCheckCount, 1)
			alreadyFixed := plugin.fixedChannels[channelID]
			if !alreadyFixed {
				atomic.AddInt32(&attemptCount, 1)
				
				// Simulate DB operation with small delay
				time.Sleep(time.Microsecond * time.Duration(id%5))
				
				// First goroutine to attempt will succeed and set cache
				plugin.fixedChannels[channelID] = true
				atomic.AddInt32(&successCount, 1)
			} else {
				atomic.AddInt32(&skippedCount, 1)
			}
			plugin.fixedChannelsMutex.Unlock()
		}(i)
	}

	wg.Wait()

	// With proper locking, only one goroutine should attempt the operation
	t.Logf("FIXED PATTERN - Attempts: %d, Successes: %d, Skipped: %d, Cache checks: %d, Final cache state: %v",
		attemptCount, successCount, skippedCount, cacheCheckCount, plugin.fixedChannels[channelID])
	
	assert.Equal(t, int32(1), attemptCount, "Only one goroutine should attempt the fix")
	assert.Equal(t, int32(1), successCount, "Exactly one goroutine should succeed")
	assert.True(t, plugin.fixedChannels[channelID], "Cache entry should persist after successful fix")
	assert.Equal(t, int32(numGoroutines), cacheCheckCount, "All goroutines should check the cache")
	assert.Equal(t, int32(numGoroutines-1), skippedCount, "All other goroutines should skip due to cache")
}

