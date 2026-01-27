package main

import (
	"database/sql/driver"
	"encoding/base64"
	"encoding/json"
	"errors"
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

// limitedReader wraps an io.Reader and enforces a maximum byte limit.
// Unlike io.LimitReader which returns EOF when limit is reached, this reader
// returns an error if more data is available beyond the limit, preventing
// silent truncation of oversized files.
type limitedReader struct {
	r         io.Reader
	limit     int64
	bytesRead int64
}

func newLimitedReader(r io.Reader, limit int64) *limitedReader {
	return &limitedReader{r: r, limit: limit}
}

var errSizeExceeded = errors.New("file size exceeds maximum allowed size")

func (lr *limitedReader) Read(p []byte) (n int, err error) {
	if lr.bytesRead >= lr.limit {
		// We've already read limit bytes. Check if there's more data to detect overflow.
		var peek [1]byte
		pn, perr := lr.r.Read(peek[:])
		if pn > 0 {
			// More data available beyond limit - file is oversized
			return 0, errSizeExceeded
		}
		// No more data (EOF) or other error
		if perr != nil {
			return 0, perr
		}
		// Shouldn't reach here (pn=0, perr=nil), but return EOF to be safe
		return 0, io.EOF
	}

	// Calculate how much we can read without exceeding limit
	maxRead := lr.limit - lr.bytesRead
	if int64(len(p)) > maxRead {
		p = p[:maxRead]
	}

	n, err = lr.r.Read(p)
	lr.bytesRead += int64(n)

	// If we've just hit exactly the limit, immediately check for overflow
	if lr.bytesRead >= lr.limit && n > 0 && err == nil {
		// We read up to the limit. Check if there's more data.
		var peek [1]byte
		pn, perr := lr.r.Read(peek[:])
		if pn > 0 {
			// More data exists - file exceeds limit
			return n, errSizeExceeded
		}
		// Return the original error from peek (EOF or other error)
		if perr != nil {
			return n, perr
		}
	}

	return n, err
}

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
	apiRouter.HandleFunc("/config/max_file_size", p.GetMaxFileSize).Methods(http.MethodGet)
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

// GetMaxFileSize returns the Mattermost FileSettings.MaxFileSize configuration value.
// This allows the backend to preflight-check attachment sizes before attempting upload.
type MaxFileSizeResponse struct {
	MaxFileSize int64  `json:"max_file_size"`
	Error       string `json:"error,omitempty"`
}

func (p *Plugin) GetMaxFileSize(w http.ResponseWriter, r *http.Request) {
	if p.API == nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(MaxFileSizeResponse{Error: "API not available"})
		return
	}

	config := p.API.GetConfig()
	if config == nil || config.FileSettings.MaxFileSize == nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(MaxFileSizeResponse{Error: "Could not retrieve file settings"})
		return
	}

	maxFileSize := *config.FileSettings.MaxFileSize
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(MaxFileSizeResponse{MaxFileSize: maxFileSize})
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
		// Ensure threadmemberships exist for thread participants (root author and reply author)
		// This fixes the issue where Mattermost's CreatePost API creates threadmembership for reply
		// author but not for the root author, causing root authors to miss thread notifications
		if err := p.ensureThreadMembershipsForReply(req.RootID, req.UserID, created.CreateAt); err != nil {
			p.API.LogWarn("Failed to ensure thread memberships for participants", "channel_id", req.ChannelID, "root_post_id", req.RootID, "error", err.Error())
			// Don't fail the request - the post was created successfully
		}

		// Mark this thread as read for all members so imported historical mentions do not generate unread counters.
		// This is called on EVERY threaded reply import to ensure that lastviewed tracks the latest reply.
		// Without this, Mattermost's CreatePost API increments unreadmentions when a reply contains @mentions.
		if err := p.markThreadAsReadForAllMembers(req.RootID, created.CreateAt); err != nil {
			p.API.LogWarn("Failed to mark thread as read for members", "channel_id", req.ChannelID, "root_post_id", req.RootID, "error", err.Error())
			// Don't fail the request if marking as read fails - the post was created successfully.
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

// uploadFileWithUserID uploads a file and sets the creator ID to the specified user.
// If userID is empty or equals model.UploadNoUserID, the file is uploaded with the plugin's context (nouser).
// Otherwise, it uploads the file and then uses CopyFileInfos to create a duplicate FileInfo
// with the correct creator ID. Note: This creates a new file record, not modifying the original.
// If setting the creator fails, it falls back to returning the original file (graceful degradation).
//
// IMPORTANT: This function creates an orphaned FileInfo record (the original upload with 'nouser' creator)
// that is not attached to any post. Mattermost's data retention policies will clean up these orphaned
// records. The physical file is NOT duplicated - both FileInfo records point to the same file on disk.
func (p *Plugin) uploadFileWithUserID(data []byte, channelID, filename, userID string) (*model.FileInfo, *model.AppError) {
	// Upload the file first using the legacy UploadFile API
	fi, appErr := p.API.UploadFile(data, channelID, filename)
	if appErr != nil {
		return nil, appErr
	}

	// If no user ID provided or it's the system user, return the file as-is
	if userID == "" || userID == model.UploadNoUserID {
		return fi, nil
	}

	// Validate that the user exists before attempting to set as creator
	user, userErr := p.API.GetUser(userID)
	if userErr != nil || user == nil {
		// User doesn't exist, return the file with system user (graceful degradation)
		p.API.LogWarn("User validation failed, keeping file with system user", "userId", userID, "fileId", fi.Id, "error", userErr)
		return fi, nil
	}

	// Use CopyFileInfos to create a duplicate FileInfo with the correct creator ID.
	// Note: This creates a new file record pointing to the same physical file.
	newFileIds, copyErr := p.API.CopyFileInfos(userID, []string{fi.Id})
	if copyErr != nil {
		// If copy fails, log warning but return the original file (graceful degradation)
		p.API.LogWarn("Failed to copy file info with user ID, using original file", "userId", userID, "fileId", fi.Id, "error", copyErr)
		return fi, nil
	}

	if len(newFileIds) == 0 {
		p.API.LogWarn("CopyFileInfos returned no file IDs, using original file", "userId", userID, "fileId", fi.Id)
		return fi, nil
	}

	// Get the new FileInfo to return
	newFi, getErr := p.API.GetFileInfo(newFileIds[0])
	if getErr != nil {
		p.API.LogWarn("Failed to get new file info, using original file", "userId", userID, "newFileId", newFileIds[0], "error", getErr)
		return fi, nil
	}

	p.API.LogDebug("Successfully created file with user ID", "userId", userID, "originalFileId", fi.Id, "newFileId", newFi.Id)
	return newFi, nil
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
	// Upload the file with the correct user ID
	fi, appErr := p.uploadFileWithUserID(data, req.ChannelID, req.Filename, req.UserID)
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
	userID := r.FormValue("user_id")
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
	fi, appErr := p.uploadFileWithUserID(data, channelID, filename, userID)
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

	// Get Mattermost MaxFileSize for preflight validation
	var maxFileSize int64
	if p.API != nil {
		config := p.API.GetConfig()
		if config != nil && config.FileSettings.MaxFileSize != nil {
			maxFileSize = *config.FileSettings.MaxFileSize
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

	// Preflight size check: reject files exceeding MaxFileSize before streaming
	if maxFileSize > 0 && contentLength > 0 && contentLength > maxFileSize {
		if p.API != nil {
			p.API.LogWarn("Rejecting oversized file before download",
				"filename", req.Filename,
				"content_length", contentLength,
				"max_file_size", maxFileSize)
		}
		w.WriteHeader(http.StatusRequestEntityTooLarge)
		_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{
			Error: fmt.Sprintf("File size %d bytes exceeds maximum allowed size %d bytes", contentLength, maxFileSize),
		})
		return
	}
	if contentLength <= 0 {
		// If content length is not provided, we need to fall back to reading all data
		data, err := io.ReadAll(resp.Body)
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: "Failed to read downloaded file"})
			return
		}
		// Upload file to Mattermost using legacy method with user ID
		fi, appErr := p.uploadFileWithUserID(data, req.ChannelID, req.Filename, req.UserID)
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
			fi, appErr := p.uploadFileWithUserID(data, req.ChannelID, req.Filename, req.UserID)
			if appErr != nil {
				w.WriteHeader(http.StatusInternalServerError)
				_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{Error: appErr.Error()})
				return
			}
			if p.API != nil {
				p.API.LogInfo("Fallback UploadFile succeeded", "channelId", req.ChannelID, "filename", req.Filename, "bytes", len(data), "fileId", fi.Id, "userId", userId)
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
	// Wrap response body with custom limitedReader to enforce hard cap on bytes read
	// and return error if file exceeds limit (prevents silent truncation)
	var reader io.Reader = resp.Body
	if maxFileSize > 0 {
		reader = newLimitedReader(resp.Body, maxFileSize)
	}

	fi, err := p.API.UploadData(us, reader)
	if err != nil {
		// Check if error is due to size exceeded
		if errors.Is(err, errSizeExceeded) {
			if p.API != nil {
				p.API.LogWarn("File exceeded size limit during streaming",
					"filename", req.Filename,
					"maxFileSize", maxFileSize)
			}
			w.WriteHeader(http.StatusRequestEntityTooLarge)
			_ = json.NewEncoder(w).Encode(UploadAttachmentResponse{
				Error: fmt.Sprintf("File exceeds maximum allowed size %d bytes", maxFileSize),
			})
			return
		}
		// Other streaming error
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

	// Execute first query and return immediately on failure to avoid partial updates
	resultUnread, err := p.Driver.ConnExec(connID, queryUnread, args)
	if err != nil {
		return err
	}
	if resultUnread.RowsAffectedError != nil {
		return resultUnread.RowsAffectedError
	}

	// Only proceed to second query if first succeeded
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

// fixInconsistentThreadMemberships fixes thread memberships where lastviewed >= lastreplyat but unreadmentions > 0.
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

	// Fix inconsistent thread memberships where lastviewed >= lastreplyat but unreadmentions > 0
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
			          AND ThreadMemberships.LastViewed >= t.LastReplyAt
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

// ClearFixedChannelsCache clears the cache of fixed channels.
// This should be called after an import session completes to prevent unbounded cache growth.
func (p *Plugin) ClearFixedChannelsCache() {
	p.fixedChannelsMutex.Lock()
	defer p.fixedChannelsMutex.Unlock()

	if p.API != nil {
		cacheSize := len(p.fixedChannels)
		if cacheSize > 0 {
			p.API.LogDebug("Clearing fixed channels cache", "cache_size", cacheSize)
		}
	}

	// Clear the cache by creating a new empty map
	p.fixedChannels = make(map[string]bool)
}

// ensureThreadMembershipsForReply ensures that threadmemberships records exist for both
// the thread root author and the reply author. This fixes the issue where CreatePost API
// creates threadmembership for the reply author but not for the root author, causing
// root authors to not receive notifications about replies to their threads.
func (p *Plugin) ensureThreadMembershipsForReply(rootPostID, replyAuthorID string, replyCreateAt int64) error {
	if p.Driver == nil || p.API == nil {
		return nil
	}

	// Get the root post to find the root author
	rootPost, appErr := p.API.GetPost(rootPostID)
	if appErr != nil {
		// If the root post does not exist (deleted, invalid ID, or not imported yet),
		// treat this as a non-fatal condition and skip ensuring threadmembership
		// for the root author. This avoids repeated failures in out-of-order import
		// scenarios where the reply arrives before the root post.
		if appErr.StatusCode == http.StatusNotFound {
			p.API.LogDebug("Root post not found when ensuring thread memberships for reply; skipping root author",
				"root_post_id", rootPostID,
				"reply_author", replyAuthorID,
				"error", appErr.Error())
			return nil
		}
		return fmt.Errorf("failed to get root post: %w", appErr)
	}

	connID, err := p.Driver.Conn(false)
	if err != nil {
		return err
	}
	defer func() {
		_ = p.Driver.ConnClose(connID)
	}()

	// Ensure threadmemberships exist for both root author and reply author
	// Using an INSERT ... SELECT pattern that works in both PostgreSQL and MySQL
	// This pattern is portable and avoids race conditions by checking existence before insert

	// For MySQL and PostgreSQL, we use INSERT with NOT EXISTS check
	// Using a direct SELECT (without subquery aliasing) is more reliably portable across both databases
	query := `INSERT INTO ThreadMemberships (PostId, UserId, Following, LastViewed, LastUpdated, UnreadMentions)
	          SELECT $1, $2, $3, $4, $5, $6
	          WHERE NOT EXISTS (
	              SELECT 1 FROM ThreadMemberships WHERE PostId = $1 AND UserId = $2
	          )`

	// Create threadmembership for root author
	args := p.makeDriverArgs(rootPostID, rootPost.UserId, true, replyCreateAt, replyCreateAt, int64(0))
	result, err := p.Driver.ConnExec(connID, query, args)
	if err != nil {
		return fmt.Errorf("failed to ensure threadmembership for root author: %w", err)
	}
	if result.RowsAffectedError != nil {
		return result.RowsAffectedError
	}

	rootAuthorInserted := result.RowsAffected > 0

	// Create threadmembership for reply author (should already exist from CreatePost, but ensure it).
	// If the reply author is the same as the root author, the first INSERT already ensured membership,
	// so we can skip a redundant second INSERT and just mirror the insertion flag.
	var replyAuthorInserted bool
	if rootPost.UserId == replyAuthorID {
		replyAuthorInserted = rootAuthorInserted
	} else {
		args = p.makeDriverArgs(rootPostID, replyAuthorID, true, replyCreateAt, replyCreateAt, int64(0))
		result, err = p.Driver.ConnExec(connID, query, args)
		if err != nil {
			return fmt.Errorf("failed to ensure threadmembership for reply author: %w", err)
		}
		if result.RowsAffectedError != nil {
			return result.RowsAffectedError
		}
		replyAuthorInserted = result.RowsAffected > 0
	}

	if rootAuthorInserted || replyAuthorInserted {
		p.API.LogDebug("Ensured threadmemberships for thread participants",
			"root_post_id", rootPostID,
			"root_author", rootPost.UserId,
			"reply_author", replyAuthorID,
			"root_author_inserted", rootAuthorInserted,
			"reply_author_inserted", replyAuthorInserted)
	}

	return nil
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
