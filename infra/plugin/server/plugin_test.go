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
	// Test that post_id field is accepted in the request (basic validation)
	// We mock a simple HTTP server to avoid nil httpClient panic
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound) // Return 404 so we fail at validation, not at nil client
	}))
	defer mockServer.Close()

	plugin := Plugin{
		httpClient: mockServer.Client(),
	}
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
	// Should get BadRequest (400) from download failure (404 response)
	assert.Equal(t, http.StatusBadRequest, result.StatusCode)
}

