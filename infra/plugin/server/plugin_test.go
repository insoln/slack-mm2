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
	// Ensure post_id is accepted without crashing; request fails during download
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
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
	assert.Equal(t, http.StatusBadRequest, result.StatusCode)
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
