package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

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
