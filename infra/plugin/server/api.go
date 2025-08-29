package main

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"

	"github.com/gorilla/mux"
	"github.com/mattermost/mattermost/server/public/model"
	"github.com/mattermost/mattermost/server/public/plugin"
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
	apiRouter.HandleFunc("/reaction", p.ImportReaction).Methods(http.MethodPost)
	apiRouter.HandleFunc("/attachment", p.UploadAttachment).Methods(http.MethodPost)
	apiRouter.HandleFunc("/attachment_multipart", p.UploadAttachmentMultipart).Methods(http.MethodPost)

	// Channel helpers
	apiRouter.HandleFunc("/channel", p.CreateOrGetChannel).Methods(http.MethodPost)
	apiRouter.HandleFunc("/channel/members", p.AddChannelMembers).Methods(http.MethodPost)
	apiRouter.HandleFunc("/channel/archive", p.ArchiveChannel).Methods(http.MethodPost)

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
	UserID    string   `json:"user_id"`
	ChannelID string   `json:"channel_id"`
	Message   string   `json:"message"`
	CreateAt  int64    `json:"create_at"`
	RootID    string   `json:"root_id,omitempty"`
	FileIDs   []string `json:"file_ids,omitempty"`
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
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(ImportPostResponse{PostID: created.Id})
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

func normalizeChannelName(name string) string {
	out := ""
	for _, r := range name {
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
	ch, appErr := p.API.GetChannelByName(name, req.TeamID, false)
	if appErr == nil && ch != nil {
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{ChannelID: ch.Id})
		return
	}
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
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: appErr.Error()})
		return
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
