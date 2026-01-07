package main

import (
	"database/sql/driver"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"unicode"

	"github.com/gorilla/mux"
	"github.com/mattermost/mattermost/server/public/model"
	"github.com/mattermost/mattermost/server/public/plugin"
	"golang.org/x/text/runes"
	"golang.org/x/text/transform"
	"golang.org/x/text/unicode/norm"
)

// ServeHTTP wires plugin REST endpoints under /api/v1.
func (p *Plugin) ServeHTTP(c *plugin.Context, w http.ResponseWriter, r *http.Request) {
	if p.API != nil {
		p.API.LogInfo("mm-importer ServeHTTP called", "path", r.URL.Path, "method", r.Method)
	}

	router := mux.NewRouter()
	apiRouter := router.PathPrefix("/api/v1").Subrouter()

	// Require authenticated system admin for all API routes
	apiRouter.Use(p.RequireAdminAuth)
	// Ensure JSON content type for all API responses
	apiRouter.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			next.ServeHTTP(w, r)
		})
	})

	apiRouter.HandleFunc("/hello", p.HelloWorld).Methods(http.MethodGet)
	apiRouter.HandleFunc("/import", p.ImportPost).Methods(http.MethodPost)
	apiRouter.HandleFunc("/import/clear_cache", p.ClearImportCache).Methods(http.MethodPost)
	apiRouter.HandleFunc("/reaction", p.ImportReaction).Methods(http.MethodPost)
	apiRouter.HandleFunc("/attachment", p.UploadAttachment).Methods(http.MethodPost)
	apiRouter.HandleFunc("/attachment_multipart", p.UploadAttachmentMultipart).Methods(http.MethodPost)
	apiRouter.HandleFunc("/attachment_from_url", p.UploadAttachmentFromURL).Methods(http.MethodPost)

	// Channel helpers
	apiRouter.HandleFunc("/channel", p.CreateOrGetChannel).Methods(http.MethodPost)
	apiRouter.HandleFunc("/channel/members", p.AddChannelMembers).Methods(http.MethodPost)
	apiRouter.HandleFunc("/channel/archive", p.ArchiveChannel).Methods(http.MethodPost)
	apiRouter.HandleFunc("/channel/unarchive", p.UnarchiveChannel).Methods(http.MethodPost)

	// DM/GDM helpers
	apiRouter.HandleFunc("/dm", p.CreateDirectChannel).Methods(http.MethodPost)
	apiRouter.HandleFunc("/gdm", p.CreateGroupChannel).Methods(http.MethodPost)

	router.ServeHTTP(w, r)
}

// RequireAdminAuth ensures the request is authenticated (Mattermost-User-ID present)
// and that the caller is a system admin. This effectively means the client must
// present a valid Mattermost user token with admin privileges.
func (p *Plugin) RequireAdminAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// In unit tests, p.API may be nil; allow requests to pass through.
		if p.API == nil {
			next.ServeHTTP(w, r)
			return
		}
		userID := r.Header.Get("Mattermost-User-ID")
		if userID == "" {
			w.WriteHeader(http.StatusUnauthorized)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "Not authorized"})
			return
		}
		user, appErr := p.API.GetUser(userID)
		if appErr != nil || user == nil {
			w.WriteHeader(http.StatusForbidden)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "Forbidden"})
			return
		}
		if !user.IsSystemAdmin() {
			w.WriteHeader(http.StatusForbidden)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "Admin required"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (p *Plugin) HelloWorld(w http.ResponseWriter, r *http.Request) {
	// Return plain text for compatibility with existing unit test
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	_, _ = w.Write([]byte("Hello, world!"))
}

// ---------------- Posts ----------------

type ImportPostRequest struct {
	UserID    string         `json:"user_id"`
	ChannelID string         `json:"channel_id"`
	Message   string         `json:"message"`
	CreateAt  int64          `json:"create_at"`
	RootID    string         `json:"root_id,omitempty"`
	FileIDs   []string       `json:"file_ids,omitempty"`
	Props     map[string]any `json:"props,omitempty"`
}

type ImportPostResponse struct {
	PostID string `json:"post_id,omitempty"`
	Error  string `json:"error,omitempty"`
}

func (p *Plugin) ImportPost(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(ImportPostResponse{Error: "Failed to read body"})
		return
	}
	var req ImportPostRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(ImportPostResponse{Error: "Invalid JSON"})
		return
	}
	if req.UserID == "" || req.ChannelID == "" || req.Message == "" {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(ImportPostResponse{Error: "user_id, channel_id, and message are required"})
		return
	}
	post := &model.Post{
		UserId:    req.UserID,
		ChannelId: req.ChannelID,
		Message:   req.Message,
		CreateAt:  req.CreateAt,
		RootId:    req.RootID,
		FileIds:   req.FileIDs,
	}
	if req.Props != nil {
		post.Props = req.Props
	}
	created, appErr := p.API.CreatePost(post)
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(ImportPostResponse{Error: appErr.Error()})
		return
	}

	// Mark the post as read for all channel members
	if err := p.markPostAsReadForChannelMembers(req.ChannelID, created.CreateAt); err != nil {
		p.API.LogWarn("Failed to mark post as read for channel members", "channel_id", req.ChannelID, "post_id", created.Id, "error", err.Error())
		// Don't fail the request if marking as read fails - the post was created successfully
	}

	// Fix inconsistent thread memberships to prevent phantom notifications
	// Only run for threaded posts (when RootID is set) to avoid unnecessary database operations
	// Use caches to avoid redundant operations for the same thread and channel during bulk imports
	if req.RootID != "" {
		// Check if this thread has already been marked as read, and if not, mark it
		// Hold the lock during check and update to avoid race conditions
		p.fixedChannelsMutex.Lock()
		alreadyMarkedThread := p.processedThreads[req.RootID]
		if !alreadyMarkedThread {
			p.processedThreads[req.RootID] = true
		}
		p.fixedChannelsMutex.Unlock()

		// Mark this thread as read for all members so imported historical mentions do not generate unread counters.
		// Only perform the operation if we haven't already processed this thread.
		// If this attempt fails, we keep the cache entry to avoid redundant or unsafe retries on
		// partially updated data (e.g., if one of the two UPDATE queries succeeded).
		if !alreadyMarkedThread {
			if err := p.markThreadAsReadForAllMembers(req.RootID, created.CreateAt); err != nil {
				p.API.LogWarn("Failed to mark thread as read for members", "channel_id", req.ChannelID, "root_post_id", req.RootID, "error", err.Error())
				// Don't fail the request if marking as read fails - the post was created successfully.
				// Cache entry remains set to prevent redundant retries that could cause inconsistent state.
			}
		}

		// Check if this channel has already been fixed, and if not, perform the fix
		// Race condition mitigation: We hold the lock during check AND DB operation to prevent
		// the following scenario: Two goroutines A and B both check alreadyFixed=false, both
		// attempt fixInconsistentThreadMemberships. If A succeeds and sets cache=true, then B
		// fails, B would incorrectly delete cache entry, causing future imports to retry.
		// Holding the lock serializes these operations, ensuring only one goroutine performs
		// the fix per channel. Performance trade-off: DB operation blocks other posts to same
		// channel, but this is acceptable as the fix runs once per channel. Consider per-channel
		// locks if this becomes a bottleneck in high-throughput scenarios.
		p.fixedChannelsMutex.Lock()
		alreadyFixed := p.fixedChannels[req.ChannelID]
		if !alreadyFixed {
			// Perform the fix while holding the lock to prevent race conditions
			if err := p.fixInconsistentThreadMemberships(req.ChannelID); err != nil {
				p.API.LogWarn("Failed to fix thread memberships", "channel_id", req.ChannelID, "post_id", created.Id, "error", err.Error())
				// Don't set cache entry on failure - allow retry on next post
			} else {
				// Success case: mark channel as fixed in cache
				// Note: If process crashes/restarts, cache is cleared and fix will run again (safe)
				p.fixedChannels[req.ChannelID] = true
			}
		}
		p.fixedChannelsMutex.Unlock()
	}

	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(ImportPostResponse{PostID: created.Id})
}

// ClearImportCache clears the fixed channels cache.
// This endpoint should be called after an import batch completes to prevent unbounded cache growth.
func (p *Plugin) ClearImportCache(w http.ResponseWriter, r *http.Request) {
	p.ClearFixedChannelsCache()
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "cache cleared"})
}

// ---------------- Reactions ----------------

type ImportReactionRequest struct {
	UserID    string `json:"user_id"`
	PostID    string `json:"post_id"`
	EmojiName string `json:"emoji_name"`
	CreateAt  int64  `json:"create_at"`
}

type ImportReactionResponse struct {
	Error string `json:"error,omitempty"`
}

func (p *Plugin) ImportReaction(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(ImportReactionResponse{Error: "Failed to read body"})
		return
	}
	var req ImportReactionRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(ImportReactionResponse{Error: "Invalid JSON"})
		return
	}
	if req.UserID == "" || req.PostID == "" || req.EmojiName == "" {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(ImportReactionResponse{Error: "user_id, post_id, and emoji_name are required"})
		return
	}
	reaction := &model.Reaction{
		UserId:    req.UserID,
		PostId:    req.PostID,
		EmojiName: req.EmojiName,
		CreateAt:  req.CreateAt,
	}
	_, appErr := p.API.AddReaction(reaction)
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(ImportReactionResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(ImportReactionResponse{})
}

// ---------------- Attachments ----------------

type UploadAttachmentRequest struct {
	ChannelID     string `json:"channel_id"`
	Filename      string `json:"filename"`
	ContentBase64 string `json:"content_base64"`
	UserID        string `json:"user_id,omitempty"`
}

type UploadAttachmentResponse struct {
	FileID string `json:"file_id,omitempty"`
	Error  string `json:"error,omitempty"`
}

func (p *Plugin) UploadAttachment(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Failed to read body"})
		return
	}
	var req UploadAttachmentRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Invalid JSON"})
		return
	}
	if req.ChannelID == "" || req.Filename == "" || req.ContentBase64 == "" {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "channel_id, filename and content_base64 are required"})
		return
	}
	data, err := base64.StdEncoding.DecodeString(req.ContentBase64)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Invalid base64 content"})
		return
	}
	// Upload the file; it will become fully downloadable via API after being attached to a post
	fi, appErr := p.API.UploadFile(data, req.ChannelID, req.Filename)
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{FileID: fi.Id})
}

// UploadAttachmentMultipart accepts multipart/form-data with fields:
// - channel_id (required)
// - filename (optional; falls back to uploaded file's name)
// - file (required) the binary content
func (p *Plugin) UploadAttachmentMultipart(w http.ResponseWriter, r *http.Request) {
	// Limit the size buffered in memory; the rest goes to temp files managed by Go
	if err := r.ParseMultipartForm(32 << 20); err != nil { // 32 MB
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Invalid multipart form"})
		return
	}
	channelID := r.FormValue("channel_id")
	if channelID == "" {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "channel_id is required"})
		return
	}
	filename := r.FormValue("filename")
	file, header, err := r.FormFile("file")
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "file is required"})
		return
	}
	defer func() { _ = file.Close() }()
	if filename == "" && header != nil {
		filename = header.Filename
	}
	if filename == "" {
		filename = "upload.bin"
	}
	// Read the file into memory as required by UploadFile API
	data, readErr := io.ReadAll(file)
	if readErr != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Failed to read file"})
		return
	}
	fi, appErr := p.API.UploadFile(data, channelID, filename)
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{FileID: fi.Id})
}

type UploadAttachmentFromURLRequest struct {
	ChannelID  string `json:"channel_id"`
	Filename   string `json:"filename"`
	FileURL    string `json:"file_url"`
	AuthHeader string `json:"auth_header"`
	UserID     string `json:"user_id,omitempty"`
	PostID     string `json:"post_id,omitempty"` // Optional: attach to existing post
}

// validatePostForAttachment checks if a post exists and belongs to the specified channel.
// This should be called before uploading files to avoid orphaned files if attachment fails.
func (p *Plugin) validatePostForAttachment(postID, channelID string) error {
	if p.API == nil {
		return nil // Skip validation in test mode
	}

	post, appErr := p.API.GetPost(postID)
	if appErr != nil {
		p.API.LogError("Post validation failed", "postId", postID, "error", appErr)
		return appErr
	}

	if post.ChannelId != channelID {
		p.API.LogError("Post channel mismatch during validation",
			"postId", postID,
			"postChannelId", post.ChannelId,
			"requestedChannelId", channelID)
		return &model.AppError{Message: "Post does not belong to the specified channel"}
	}

	return nil
}

// attachFileToPost appends a file_id to an existing post's FileIds.
// Assumes post has already been validated via validatePostForAttachment.
// Returns error if update fails.
func (p *Plugin) attachFileToPost(postID, fileID string) error {
	if p.API == nil {
		return nil // Skip attachment in test mode
	}

	// Fetch the post (we know it exists from validation)
	post, appErr := p.API.GetPost(postID)
	if appErr != nil {
		return appErr
	}
	originalMessage := post.Message
	targetEditAt := post.EditAt
	targetUpdateAt := post.UpdateAt
	if targetUpdateAt == 0 {
		targetUpdateAt = post.CreateAt
	}

	// Append file to the post's FileIds
	if post.FileIds == nil {
		post.FileIds = []string{}
	}
	post.FileIds = append(post.FileIds, fileID)

	// Update the post
	updatedPost, appErr := p.API.UpdatePost(post)
	if appErr != nil {
		p.API.LogError("Failed to update post with file attachment",
			"postId", postID,
			"fileId", fileID,
			"error", appErr)
		return appErr
	}
	if updatedPost != nil {
		post = updatedPost
	}
	p.restoreOriginalPostTimestamps(post, originalMessage, targetEditAt, targetUpdateAt)

	p.API.LogDebug("Successfully attached file to existing post",
		"postId", postID,
		"fileId", fileID)
	return nil
}

// restoreOriginalPostTimestamps resets EditAt/UpdateAt after UpdatePost forces a fresh timestamp.
// We only run this when the post message text is unchanged to avoid hiding legitimate edits.
func (p *Plugin) restoreOriginalPostTimestamps(post *model.Post, originalMessage string, targetEditAt, targetUpdateAt int64) {
	if p.Driver == nil || p.API == nil || post == nil {
		return
	}
	if targetUpdateAt == 0 {
		targetUpdateAt = post.CreateAt
	}
	if post.Message != originalMessage {
		// Log when restoration is skipped due to message changes so admins can identify
		// posts with legitimate vs. spurious edit timestamps
		p.API.LogDebug("restoreOriginalPostTimestamps: skipped due to message change",
			"postId", post.Id,
			"originalLength", len(originalMessage),
			"currentLength", len(post.Message))
		return
	}
	if post.EditAt == targetEditAt && post.UpdateAt == targetUpdateAt {
		return
	}
	connID, err := p.Driver.Conn(false)
	if err != nil {
		p.API.LogWarn("restoreOriginalPostTimestamps: failed to acquire connection",
			"postId", post.Id,
			"error", err.Error())
		return
	}
	defer func() {
		_ = p.Driver.ConnClose(connID)
	}()

	query := `UPDATE Posts
			SET EditAt = $1,
			    UpdateAt = $2
			WHERE Id = $3
			  AND Message = $4
			  AND (EditAt <> $1 OR UpdateAt <> $2)`
	args := p.makeDriverArgs(targetEditAt, targetUpdateAt, post.Id, originalMessage)
	result, execErr := p.Driver.ConnExec(connID, query, args)
	if execErr != nil {
		p.API.LogWarn("restoreOriginalPostTimestamps: update failed",
			"postId", post.Id,
			"error", execErr.Error())
		return
	}
	if result.RowsAffectedError != nil {
		p.API.LogWarn("restoreOriginalPostTimestamps: rows affected error",
			"postId", post.Id,
			"error", result.RowsAffectedError)
		return
	}
	if result.RowsAffected > 0 {
		p.API.LogDebug("Restored original post timestamps after attachment",
			"postId", post.Id,
			"editAt", targetEditAt,
			"updateAt", targetUpdateAt,
			"rows", result.RowsAffected)
	}
}

// UploadAttachmentFromURL downloads a file from the specified URL and uploads it to Mattermost.
// If post_id is provided, the file is attached to that existing post after upload.
func (p *Plugin) UploadAttachmentFromURL(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Failed to read body"})
		return
	}
	var req UploadAttachmentFromURLRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Invalid JSON"})
		return
	}
	if req.ChannelID == "" || req.Filename == "" || req.FileURL == "" || req.AuthHeader == "" {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "channel_id, filename, file_url and auth_header are required"})
		return
	}

	// Validate post exists and belongs to channel before uploading to avoid orphaned files
	if req.PostID != "" {
		if err := p.validatePostForAttachment(req.PostID, req.ChannelID); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: err.Error()})
			return
		}
	}

	// Use the shared plugin HTTP client to download the file
	httpReq, err := http.NewRequest("GET", req.FileURL, nil)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Invalid file URL"})
		return
	}
	httpReq.Header.Set("Authorization", req.AuthHeader)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Failed to download file"})
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "File download failed"})
		return
	}

	// Get content length for upload session
	contentLength := resp.ContentLength
	if contentLength <= 0 {
		// If content length is not provided, we need to fall back to reading all data
		data, err := io.ReadAll(resp.Body)
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Failed to read downloaded file"})
			return
		}
		// Upload file to Mattermost using legacy method
		fi, appErr := p.API.UploadFile(data, req.ChannelID, req.Filename)
		if appErr != nil {
			w.WriteHeader(http.StatusInternalServerError)
			_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: appErr.Error()})
			return
		}

		// Attach to existing post if post_id provided
		if req.PostID != "" {
			if err := p.attachFileToPost(req.PostID, fi.Id); err != nil {
				w.WriteHeader(http.StatusBadRequest)
				_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: err.Error()})
				return
			}
		}

		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{FileID: fi.Id})
		return
	}

	// Create upload session for streaming
	// Use provided user ID if available, otherwise fallback to system user ID
	userId := req.UserID
	if userId == "" {
		userId = model.UploadNoUserID
	}

	// Validate that the user exists in Mattermost if it's not the system user
	if userId != model.UploadNoUserID && userId != "" {
		if p.API != nil {
			user, appErr := p.API.GetUser(userId)
			if appErr != nil || user == nil {
				// User doesn't exist or API call failed, fallback to system user
				if p.API != nil {
					p.API.LogWarn("User validation failed for attachment upload, using system user", "userId", userId, "error", appErr)
				}
				userId = model.UploadNoUserID
			}
		} else {
			// API not available, use system user
			userId = model.UploadNoUserID
		}
	} else {
		// Empty or already system user, ensure it's properly set
		userId = model.UploadNoUserID
	}

	uploadSession := &model.UploadSession{
		Type:      model.UploadTypeAttachment,
		UserId:    userId,
		ChannelId: req.ChannelID,
		Filename:  req.Filename,
		FileSize:  contentLength,
	}

	// Validate channel exists
	if p.API != nil {
		channel, appErr := p.API.GetChannel(req.ChannelID)
		if appErr != nil || channel == nil {
			p.API.LogError("Channel validation failed for attachment upload", "channelId", req.ChannelID, "error", appErr)
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Invalid channel ID"})
			return
		}
		p.API.LogDebug("Creating upload session", "userId", userId, "channelId", req.ChannelID, "filename", req.Filename, "fileSize", contentLength, "originalUserId", req.UserID)
	}

	// Create upload session if API available
	var us *model.UploadSession
	if p.API != nil {
		var err error
		us, err = p.API.CreateUploadSession(uploadSession)
		if err != nil {
			// Fallback: read entire body and use legacy UploadFile path so we don't fail the whole attachment.
			if p.API != nil {
				p.API.LogWarn("CreateUploadSession failed, falling back to legacy UploadFile", "error", err.Error(), "channelId", req.ChannelID, "filename", req.Filename, "fileSize", contentLength, "userIdEffective", userId, "userIdRequested", req.UserID)
			}
			data, rerr := io.ReadAll(resp.Body)
			if rerr != nil {
				w.WriteHeader(http.StatusInternalServerError)
				_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Failed to read downloaded file (fallback)"})
				return
			}
			fi, appErr := p.API.UploadFile(data, req.ChannelID, req.Filename)
			if appErr != nil {
				w.WriteHeader(http.StatusInternalServerError)
				_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: appErr.Error()})
				return
			}
			if p.API != nil {
				p.API.LogInfo("Fallback UploadFile succeeded", "channelId", req.ChannelID, "filename", req.Filename, "bytes", len(data), "fileId", fi.Id)
			}

			// Attach to existing post if post_id provided
			if req.PostID != "" {
				if err := p.attachFileToPost(req.PostID, fi.Id); err != nil {
					w.WriteHeader(http.StatusBadRequest)
					_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: err.Error()})
					return
				}
			}

			w.WriteHeader(http.StatusOK)
			_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{FileID: fi.Id})
			return
		}
	} else {
		// Test mode without API
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "API not available (test mode)"})
		return
	}

	// Stream file data to Mattermost (happy path)
	fi, err := p.API.UploadData(us, resp.Body)
	if err != nil {
		// On streaming error, attempt one-time fallback to legacy read+UploadFile if body still readable
		data, rerr := io.ReadAll(resp.Body)
		if rerr == nil && len(data) > 0 {
			if p.API != nil {
				p.API.LogWarn("Streaming UploadData failed; retrying with legacy UploadFile", "error", err.Error(), "channelId", req.ChannelID, "filename", req.Filename, "bytes", len(data))
			}
			if fi2, appErr := p.API.UploadFile(data, req.ChannelID, req.Filename); appErr == nil {
				if p.API != nil {
					p.API.LogInfo("Fallback after stream succeeded", "channelId", req.ChannelID, "filename", req.Filename, "fileId", fi2.Id)
				}

				// Attach to existing post if post_id provided
				if req.PostID != "" {
					if err := p.attachFileToPost(req.PostID, fi2.Id); err != nil {
						w.WriteHeader(http.StatusBadRequest)
						_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: err.Error()})
						return
					}
				}

				w.WriteHeader(http.StatusOK)
				_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{FileID: fi2.Id})
				return
			}
		}
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: err.Error()})
		return
	}
	if p.API != nil {
		p.API.LogDebug("Streaming upload succeeded", "channelId", req.ChannelID, "filename", req.Filename, "fileId", fi.Id)
	}

	// Attach to existing post if post_id provided
	if req.PostID != "" {
		if err := p.attachFileToPost(req.PostID, fi.Id); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: err.Error()})
			return
		}
	}

	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{FileID: fi.Id})
}

// ---------------- Channel helpers ----------------

type CreateOrGetChannelRequest struct {
	TeamID      string `json:"team_id"`
	Name        string `json:"name"`
	DisplayName string `json:"display_name"`
	Type        string `json:"type"` // "O" or "P"
	Header      string `json:"header,omitempty"`
	Purpose     string `json:"purpose,omitempty"`
}

type CreateOrGetChannelResponse struct {
	ChannelID string `json:"channel_id,omitempty"`
	Error     string `json:"error,omitempty"`
}

// transliterateCyrillic converts Cyrillic characters to their Latin equivalents.
// This helps preserve channel name uniqueness when dealing with Cyrillic names.
func transliterateCyrillic(r rune) string {
	translitMap := map[rune]string{
		'а': "a", 'б': "b", 'в': "v", 'г': "g", 'д': "d", 'е': "e", 'ё': "yo",
		'ж': "zh", 'з': "z", 'и': "i", 'й': "y", 'к': "k", 'л': "l", 'м': "m",
		'н': "n", 'о': "o", 'п': "p", 'р': "r", 'с': "s", 'т': "t", 'у': "u",
		'ф': "f", 'х': "h", 'ц': "ts", 'ч': "ch", 'ш': "sh", 'щ': "sch", 'ъ': "",
		'ы': "y", 'ь': "", 'э': "e", 'ю': "yu", 'я': "ya",
		'А': "a", 'Б': "b", 'В': "v", 'Г': "g", 'Д': "d", 'Е': "e", 'Ё': "yo",
		'Ж': "zh", 'З': "z", 'И': "i", 'Й': "y", 'К': "k", 'Л': "l", 'М': "m",
		'Н': "n", 'О': "o", 'П': "p", 'Р': "r", 'С': "s", 'Т': "t", 'У': "u",
		'Ф': "f", 'Х': "h", 'Ц': "ts", 'Ч': "ch", 'Ш': "sh", 'Щ': "sch", 'Ъ': "",
		'Ы': "y", 'Ь': "", 'Э': "e", 'Ю': "yu", 'Я': "ya",
	}
	if trans, ok := translitMap[r]; ok {
		return trans
	}
	return ""
}

// normalizeUnicode applies Unicode normalization to convert non-ASCII characters to ASCII.
// Uses NFD (Normalized Form Decomposed) to separate base characters from diacritics,
// then removes diacritics and non-Latin characters. Cyrillic gets special transliteration.
// Examples:
//   - "café" → "cafe"
//   - "naïve" → "naive"
//   - "маркетинг" → "marketing"
//   - "日本語" → "" (no Latin equivalent, will be handled by fallback)
func normalizeUnicode(s string) string {
	// First pass: Cyrillic transliteration for better readability
	var cyrillic strings.Builder
	for _, r := range s {
		if trans := transliterateCyrillic(r); trans != "" {
			cyrillic.WriteString(trans)
		} else {
			cyrillic.WriteRune(r)
		}
	}
	
	// Second pass: NFD normalization to decompose characters
	// Example: "é" → "e" + combining accent
	t := transform.Chain(norm.NFD, runes.Remove(runes.In(unicode.Mn)), norm.NFC)
	result, _, err := transform.String(t, cyrillic.String())
	if err != nil {
		// Transform errors are rare for NFD normalization, but log if they occur
		// to aid debugging of unexpected Unicode input
		return cyrillic.String() // Fallback to Cyrillic-only transliteration
	}
	
	return result
}

func normalizeChannelName(name string) string {
	// Apply Unicode normalization first
	normalized := normalizeUnicode(name)
	
	out := ""
	for _, r := range normalized {
		switch {
		case r >= 'a' && r <= 'z':
			out += string(r)
		case r >= '0' && r <= '9':
			out += string(r)
		case r >= 'A' && r <= 'Z':
			out += string(r + 32)
		case r == '-' || r == '_' || r == ' ' || r == '.':
			out += "-"
		default:
			// After Unicode normalization, remaining non-ASCII is dropped
		}
	}
	cleaned := ""
	prevDash := false
	for _, r := range out {
		if r == '-' {
			if prevDash {
				continue
			}
			prevDash = true
		} else {
			prevDash = false
		}
		cleaned += string(r)
	}
	for len(cleaned) > 0 && cleaned[0] == '-' {
		cleaned = cleaned[1:]
	}
	for len(cleaned) > 0 && cleaned[len(cleaned)-1] == '-' {
		cleaned = cleaned[:len(cleaned)-1]
	}
	if len(cleaned) == 0 {
		cleaned = "ch-" + model.NewId()[:6]
	}
	if len(cleaned) < 2 {
		cleaned = cleaned + "-" + model.NewId()[:2]
	}
	if len(cleaned) > 64 {
		cleaned = cleaned[:64]
	}
	return cleaned
}

func (p *Plugin) CreateOrGetChannel(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: "Failed to read body"})
		return
	}
	var req CreateOrGetChannelRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: "Invalid JSON"})
		return
	}
	if req.TeamID == "" || req.Name == "" || (req.Type != "O" && req.Type != "P") {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: "team_id, name and valid type are required"})
		return
	}
	name := normalizeChannelName(req.Name)
	
	// Log normalization for debugging
	if p.API != nil && name != req.Name {
		p.API.LogDebug("Channel name normalized", "original", req.Name, "normalized", name)
	}
	
	// First try to find existing channel, INCLUDING archived/deleted channels
	ch, appErr := p.API.GetChannelByName(name, req.TeamID, true)
	if appErr == nil && ch != nil {
		if p.API != nil {
			p.API.LogDebug("Found existing channel (possibly archived)", "name", name, "channel_id", ch.Id, "delete_at", ch.DeleteAt)
		}
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{ChannelID: ch.Id})
		return
	}
	
	// Try to create the channel
	channel := &model.Channel{
		TeamId:      req.TeamID,
		Name:        name,
		DisplayName: req.DisplayName,
		Type:        model.ChannelType(req.Type),
		Header:      req.Header,
		Purpose:     req.Purpose,
	}
	created, appErr := p.API.CreateChannel(channel)
	if appErr != nil {
		// If creation failed due to name conflict, try with a suffix
		errStr := appErr.Error()
		if strings.Contains(strings.ToLower(errStr), "already exists") {
			if p.API != nil {
				p.API.LogWarn("Channel name conflict detected, trying with suffix", "name", name, "error", errStr)
			}
			
			// Try to find the existing channel again with includeDeleted=true
			// This handles race conditions where channel was created between our check and creation attempt
			ch, lookupErr := p.API.GetChannelByName(name, req.TeamID, true)
			if lookupErr == nil && ch != nil {
				if p.API != nil {
					p.API.LogInfo("Found existing channel after conflict", "name", name, "channel_id", ch.Id)
				}
				w.WriteHeader(http.StatusOK)
				_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{ChannelID: ch.Id})
				return
			}
			
			// If still not found, try creating with auto-suffix
			suffix := model.NewId()[:6]
			newName := name
			if len(name) > 57 { // Reserve space for -suffix (7 chars)
				newName = name[:57]
			}
			newName = newName + "-" + suffix
			
			channel.Name = newName
			if p.API != nil {
				p.API.LogInfo("Retrying channel creation with suffix", "original_name", name, "new_name", newName)
			}
			
			created, appErr = p.API.CreateChannel(channel)
			if appErr != nil {
				if p.API != nil {
					p.API.LogError("Failed to create channel even with suffix", "name", newName, "error", appErr.Error())
				}
				w.WriteHeader(http.StatusInternalServerError)
				_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: appErr.Error()})
				return
			}
		} else {
			// Non-conflict error
			w.WriteHeader(http.StatusInternalServerError)
			_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: appErr.Error()})
			return
		}
	}
	
	if p.API != nil {
		p.API.LogDebug("Channel created successfully", "name", channel.Name, "channel_id", created.Id)
	}
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{ChannelID: created.Id})
}

type AddChannelMembersRequest struct {
	ChannelID string   `json:"channel_id"`
	UserIDs   []string `json:"user_ids"`
}

type AddChannelMembersResponse struct {
	Added []string `json:"added,omitempty"`
	Error string   `json:"error,omitempty"`
}

func (p *Plugin) AddChannelMembers(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(AddChannelMembersResponse{Error: "Failed to read body"})
		return
	}
	var req AddChannelMembersRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(AddChannelMembersResponse{Error: "Invalid JSON"})
		return
	}
	if req.ChannelID == "" || len(req.UserIDs) == 0 {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(AddChannelMembersResponse{Error: "channel_id and user_ids are required"})
		return
	}
	added := make([]string, 0, len(req.UserIDs))
	for _, uid := range req.UserIDs {
		if uid == "" {
			continue
		}
		if _, appErr := p.API.AddChannelMember(req.ChannelID, uid); appErr != nil {
			p.API.LogWarn("AddChannelMember failed", "channel_id", req.ChannelID, "user_id", uid, "error", appErr.Error())
			continue
		}
		added = append(added, uid)
	}
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(AddChannelMembersResponse{Added: added})
}

type ArchiveChannelRequest struct {
	ChannelID string `json:"channel_id"`
}

type ArchiveChannelResponse struct {
	Error string `json:"error,omitempty"`
}

func (p *Plugin) ArchiveChannel(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(ArchiveChannelResponse{Error: "Failed to read body"})
		return
	}
	var req ArchiveChannelRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(ArchiveChannelResponse{Error: "Invalid JSON"})
		return
	}
	if req.ChannelID == "" {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(ArchiveChannelResponse{Error: "channel_id is required"})
		return
	}
	if appErr := p.API.DeleteChannel(req.ChannelID); appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(ArchiveChannelResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(ArchiveChannelResponse{})
}

type UnarchiveChannelRequest struct {
	ChannelID string `json:"channel_id"`
}

type UnarchiveChannelResponse struct {
	Error string `json:"error,omitempty"`
}

func (p *Plugin) UnarchiveChannel(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UnarchiveChannelResponse{Error: "Failed to read body"})
		return
	}
	var req UnarchiveChannelRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UnarchiveChannelResponse{Error: "Invalid JSON"})
		return
	}
	if req.ChannelID == "" {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(UnarchiveChannelResponse{Error: "channel_id is required"})
		return
	}
	// Get the channel first
	channel, appErr := p.API.GetChannel(req.ChannelID)
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(UnarchiveChannelResponse{Error: appErr.Error()})
		return
	}
	// Unarchive by setting DeleteAt to 0
	channel.DeleteAt = 0
	_, appErr = p.API.UpdateChannel(channel)
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(UnarchiveChannelResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(UnarchiveChannelResponse{})
}

// ---------------- DM / GDM ----------------

type CreateDMRequest struct {
	UserIDs []string `json:"user_ids"` // exactly 2
}

type CreateDMResponse struct {
	ChannelID string `json:"channel_id,omitempty"`
	Error     string `json:"error,omitempty"`
}

func (p *Plugin) CreateDirectChannel(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(CreateDMResponse{Error: "Failed to read body"})
		return
	}
	var req CreateDMRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(CreateDMResponse{Error: "Invalid JSON"})
		return
	}
	if len(req.UserIDs) != 2 {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(CreateDMResponse{Error: "user_ids must contain exactly 2 ids"})
		return
	}
	ch, appErr := p.API.GetDirectChannel(req.UserIDs[0], req.UserIDs[1])
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(CreateDMResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(CreateDMResponse{ChannelID: ch.Id})
}

type CreateGDMRequest struct {
	UserIDs []string `json:"user_ids"` // 3..n
}

type CreateGDMResponse struct {
	ChannelID string `json:"channel_id,omitempty"`
	Error     string `json:"error,omitempty"`
}

func (p *Plugin) CreateGroupChannel(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(CreateGDMResponse{Error: "Failed to read body"})
		return
	}
	var req CreateGDMRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(CreateGDMResponse{Error: "Invalid JSON"})
		return
	}
	if len(req.UserIDs) < 3 {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(CreateGDMResponse{Error: "user_ids must contain at least 3 ids"})
		return
	}
	ch, appErr := p.API.GetGroupChannel(req.UserIDs)
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(CreateGDMResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(CreateGDMResponse{ChannelID: ch.Id})
}

// markPostAsReadForChannelMembers updates LastViewedAt for all members of a channel in a single SQL statement.
// This prevents false notifications during bulk import operations without issuing N API/database calls.
func (p *Plugin) markPostAsReadForChannelMembers(channelID string, postCreateAt int64) error {
	if p.Driver == nil {
		p.API.LogDebug("Driver not available, skipping LastViewedAt update", "channel_id", channelID)
		return nil
	}

	connID, err := p.Driver.Conn(false) // false = not in transaction
	if err != nil {
		return err
	}
	defer func() {
		_ = p.Driver.ConnClose(connID)
	}()

	includeMsgCounts := false
	var channelMsgCount int64
	var channelMsgCountRoot int64

	if p.API != nil {
		if ch, appErr := p.API.GetChannel(channelID); appErr == nil && ch != nil {
			channelMsgCount = ch.TotalMsgCount
			channelMsgCountRoot = ch.TotalMsgCountRoot
			includeMsgCounts = true
		} else if appErr != nil {
			p.API.LogWarn("Unable to fetch channel totals for mark-as-read",
				"channel_id", channelID,
				"error", appErr.Error())
		}
	}

	query := `UPDATE ChannelMembers
			  SET LastViewedAt = CASE WHEN LastViewedAt < $1 THEN $1 ELSE LastViewedAt END,
			      MentionCount = 0,
			      MentionCountRoot = 0`

	// Parameter order keeps $1 bound to the post timestamp and $2 to the ChannelId used in the WHERE clause.
	args := p.makeDriverArgs(postCreateAt, channelID)
	whereParts := []string{"LastViewedAt < $1", "MentionCount <> 0", "MentionCountRoot <> 0"}

	if includeMsgCounts {
		nextIdx := len(args) + 1
		query += fmt.Sprintf(",\n                  MsgCount = $%d,\n                  MsgCountRoot = $%d", nextIdx, nextIdx+1)
		args = append(args,
			driver.NamedValue{Ordinal: nextIdx, Value: channelMsgCount},
			driver.NamedValue{Ordinal: nextIdx + 1, Value: channelMsgCountRoot},
		)
		whereParts = append(whereParts,
			fmt.Sprintf("MsgCount <> $%d", nextIdx),
			fmt.Sprintf("MsgCountRoot <> $%d", nextIdx+1),
		)
	}

	query += fmt.Sprintf(`
			  WHERE ChannelId = $2 AND (%s)`, strings.Join(whereParts, " OR "))

	result, err := p.Driver.ConnExec(connID, query, args)
	if err != nil {
		return err
	}

	if result.RowsAffectedError != nil {
		return result.RowsAffectedError
	}

	rowsAffected := result.RowsAffected
	if rowsAffected == 0 {
		p.API.LogDebug("No channel members required mark-as-read update",
			"channel_id", channelID,
			"timestamp", postCreateAt)
		return nil
	}

	logFields := []interface{}{"channel_id", channelID, "timestamp", postCreateAt, "rows_affected", rowsAffected}
	if includeMsgCounts {
		logFields = append(logFields, "msg_count", channelMsgCount, "msg_count_root", channelMsgCountRoot)
	}

	p.API.LogDebug("Marked posts as read for channel members", logFields...)

	return nil
}

// markThreadAsReadForAllMembers updates ThreadMemberships for a thread root so imported historical
// thread mentions do not show up as unread for users on a fresh import.
func (p *Plugin) markThreadAsReadForAllMembers(threadRootPostID string, lastViewedAt int64) error {
	if p.Driver == nil {
		if p.API != nil {
			p.API.LogDebug("Driver not available, skipping thread mark-as-read", "post_id", threadRootPostID)
		}
		return nil
	}

	connID, err := p.Driver.Conn(false)
	if err != nil {
		return err
	}
	defer func() {
		_ = p.Driver.ConnClose(connID)
	}()

	// Split the OR condition into two separate UPDATEs so indexes can be used more efficiently.
	// Case 1: rows with non-zero UnreadMentions (regardless of LastViewed).
	queryUnread := `UPDATE ThreadMemberships
			  SET LastViewed = CASE WHEN LastViewed < $2 THEN $2 ELSE LastViewed END,
			      UnreadMentions = 0
			  WHERE PostId = $1
			    AND UnreadMentions <> 0`

	// Case 2: rows with zero UnreadMentions but outdated LastViewed.
	queryLastViewed := `UPDATE ThreadMemberships
			  SET LastViewed = $2
			  WHERE PostId = $1
			    AND UnreadMentions = 0
			    AND LastViewed < $2`

	args := p.makeDriverArgs(threadRootPostID, lastViewedAt)

	resultUnread, err := p.Driver.ConnExec(connID, queryUnread, args)
	if err != nil {
		return err
	}
	if resultUnread.RowsAffectedError != nil {
		return resultUnread.RowsAffectedError
	}

	resultLastViewed, err := p.Driver.ConnExec(connID, queryLastViewed, args)
	if err != nil {
		return err
	}
	if resultLastViewed.RowsAffectedError != nil {
		return resultLastViewed.RowsAffectedError
	}

	totalRows := resultUnread.RowsAffected + resultLastViewed.RowsAffected

	if totalRows > 0 {
		if p.API != nil {
			p.API.LogDebug("Marked thread as read for all members",
				"post_id", threadRootPostID,
				"last_viewed", lastViewedAt,
				"rows_affected", totalRows)
		}
	}

	return nil
}

// fixInconsistentThreadMemberships fixes thread memberships where lastviewed > lastreplyat but unreadmentions > 0.
// This prevents phantom notification counters after bulk imports.
// It also sets threadteamid for threads that are missing it (common for DM channels).
func (p *Plugin) fixInconsistentThreadMemberships(channelID string) error {
	if p.Driver == nil {
		if p.API != nil {
			p.API.LogDebug("Driver not available, skipping thread membership fix", "channel_id", channelID)
		}
		return nil
	}

	connID, err := p.Driver.Conn(false)
	if err != nil {
		return err
	}
	defer func() {
		_ = p.Driver.ConnClose(connID)
	}()

	// Get channel to determine team ID for setting threadteamid
	var teamID string
	if p.API != nil {
		if ch, appErr := p.API.GetChannel(channelID); appErr == nil && ch != nil {
			teamID = ch.TeamId
		}
	}

	// Fix inconsistent thread memberships where lastviewed > lastreplyat but unreadmentions > 0
	// This is the core fix for the phantom notifications issue
	// Note: This query operates on a per-channel basis to minimize impact.
	// Uses subquery approach for compatibility with both PostgreSQL and MySQL.
	// For optimal performance, Mattermost should have indexes on:
	//   - ThreadMemberships(PostId, UnreadMentions, LastViewed)
	//   - Threads(ChannelId, PostId, LastReplyAt)
	query := `UPDATE ThreadMemberships
			  SET UnreadMentions = 0
			  WHERE UnreadMentions > 0
			    AND EXISTS (
			        SELECT 1
			        FROM Threads t
			        WHERE t.PostId = ThreadMemberships.PostId
			          AND t.ChannelId = $1
			          AND ThreadMemberships.LastViewed > t.LastReplyAt
			    )`

	args := p.makeDriverArgs(channelID)
	result, err := p.Driver.ConnExec(connID, query, args)
	if err != nil {
		return err
	}

	if result.RowsAffectedError != nil {
		return result.RowsAffectedError
	}

	rowsAffected := result.RowsAffected
	if rowsAffected > 0 {
		if p.API != nil {
			p.API.LogDebug("Fixed inconsistent thread memberships",
				"channel_id", channelID,
				"rows_affected", rowsAffected)
		}
	}

	// Optionally set threadteamid for threads missing it (common in DM channels)
	// This helps with query planning and performance (64% of threads in production are DMs with empty team IDs)
	// Note: For optimal performance, an index on Threads(ChannelId, ThreadTeamId) is recommended
	// Query is compatible with both PostgreSQL and MySQL
	if teamID != "" {
		teamQuery := `UPDATE Threads
					  SET ThreadTeamId = $1
					  WHERE ChannelId = $2
					    AND (ThreadTeamId IS NULL OR ThreadTeamId = '')`

		teamArgs := p.makeDriverArgs(teamID, channelID)
		teamResult, teamErr := p.Driver.ConnExec(connID, teamQuery, teamArgs)
		if teamErr != nil {
			// Log but don't fail - this is optional optimization
			if p.API != nil {
				p.API.LogWarn("Failed to set threadteamid", "channel_id", channelID, "error", teamErr.Error())
			}
		} else if teamResult.RowsAffectedError == nil && teamResult.RowsAffected > 0 {
			if p.API != nil {
				p.API.LogDebug("Set threadteamid for threads",
					"channel_id", channelID,
					"team_id", teamID,
					"rows_affected", teamResult.RowsAffected)
			}
		}
	}

	return nil
}

// ClearFixedChannelsCache clears the cache of fixed channels and processed threads.
// This should be called after an import session completes to prevent unbounded cache growth.
func (p *Plugin) ClearFixedChannelsCache() {
	p.fixedChannelsMutex.Lock()
	defer p.fixedChannelsMutex.Unlock()

	if p.API != nil {
		channelsCacheSize := len(p.fixedChannels)
		threadsCacheSize := len(p.processedThreads)
		totalCacheSize := channelsCacheSize + threadsCacheSize
		if totalCacheSize > 0 {
			p.API.LogDebug("Clearing import caches",
				"channels_cached", channelsCacheSize,
				"threads_cached", threadsCacheSize,
				"total_items", totalCacheSize)
		}
	}

	// Clear the caches by creating new empty maps
	p.fixedChannels = make(map[string]bool)
	p.processedThreads = make(map[string]bool)
}

// makeDriverArgs converts variadic arguments to driver.NamedValue slice
func (p *Plugin) makeDriverArgs(values ...interface{}) []driver.NamedValue {
	args := make([]driver.NamedValue, len(values))
	for i, v := range values {
		args[i] = driver.NamedValue{
			Ordinal: i + 1,
			Value:   v,
		}
	}
	return args
}
