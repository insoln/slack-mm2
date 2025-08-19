package main

import (
	"encoding/json"
	"io/ioutil"
	"net/http"

	"github.com/gorilla/mux"
	"github.com/mattermost/mattermost/server/public/model"
	"github.com/mattermost/mattermost/server/public/plugin"
)

// ServeHTTP demonstrates a plugin that handles HTTP requests by greeting the world.
// The root URL is currently <siteUrl>/plugins/com.mattermost.plugin-starter-template/api/v1/. Replace com.mattermost.plugin-starter-template with the plugin ID.
func (p *Plugin) ServeHTTP(c *plugin.Context, w http.ResponseWriter, r *http.Request) {
	p.API.LogInfo("mm-importer ServeHTTP called", "path", r.URL.Path, "method", r.Method)
	router := mux.NewRouter()

	apiRouter := router.PathPrefix("/api/v1").Subrouter()

	apiRouter.HandleFunc("/hello", p.HelloWorld).Methods(http.MethodGet)
	apiRouter.HandleFunc("/import", p.ImportPost).Methods(http.MethodPost)
	apiRouter.HandleFunc("/reaction", p.ImportReaction).Methods(http.MethodPost)
	// New endpoints for channel operations to support Slack import gaps
	apiRouter.HandleFunc("/channel", p.CreateOrGetChannel).Methods(http.MethodPost)
	apiRouter.HandleFunc("/channel/members", p.AddChannelMembers).Methods(http.MethodPost)
	apiRouter.HandleFunc("/channel/archive", p.ArchiveChannel).Methods(http.MethodPost)
	apiRouter.HandleFunc("/dm", p.CreateDirectChannel).Methods(http.MethodPost)
	apiRouter.HandleFunc("/gdm", p.CreateGroupChannel).Methods(http.MethodPost)

	router.ServeHTTP(w, r)
}

func (p *Plugin) MattermostAuthorizationRequired(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		userID := r.Header.Get("Mattermost-User-ID")
		if userID == "" {
			http.Error(w, "Not authorized", http.StatusUnauthorized)
			return
		}

		next.ServeHTTP(w, r)
	})
}

func (p *Plugin) HelloWorld(w http.ResponseWriter, r *http.Request) {
	if _, err := w.Write([]byte("Hello, world!")); err != nil {
		p.API.LogError("Failed to write response", "error", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

type ImportPostRequest struct {
	UserID    string `json:"user_id"`
	ChannelID string `json:"channel_id"`
	Message   string `json:"message"`
	CreateAt  int64  `json:"create_at"`
	RootID    string `json:"root_id,omitempty"`
}

type ImportPostResponse struct {
	PostID string `json:"post_id,omitempty"`
	Error  string `json:"error,omitempty"`
}

func (p *Plugin) ImportPost(w http.ResponseWriter, r *http.Request) {
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ImportPostResponse{Error: "Failed to read body"})
		return
	}

	var req ImportPostRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ImportPostResponse{Error: "Invalid JSON"})
		return
	}

	if req.UserID == "" || req.ChannelID == "" || req.Message == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ImportPostResponse{Error: "user_id, channel_id, and message are required"})
		return
	}

	post := &model.Post{
		UserId:    req.UserID,
		ChannelId: req.ChannelID,
		Message:   req.Message,
		CreateAt:  req.CreateAt,
		RootId:    req.RootID,
	}

	created, appErr := p.API.CreatePost(post)
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(ImportPostResponse{Error: appErr.Error()})
		return
	}

	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(ImportPostResponse{PostID: created.Id})
}

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
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ImportReactionResponse{Error: "Failed to read body"})
		return
	}

	var req ImportReactionRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ImportReactionResponse{Error: "Invalid JSON"})
		return
	}

	if req.UserID == "" || req.PostID == "" || req.EmojiName == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ImportReactionResponse{Error: "user_id, post_id, and emoji_name are required"})
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
		json.NewEncoder(w).Encode(ImportReactionResponse{Error: appErr.Error()})
		return
	}

	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(ImportReactionResponse{})
}

// ---- Channel helpers & endpoints ----

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
	// Mattermost requires: 2-64 chars, lowercase letters, numbers, and dashes only.
	// Strategy: lowercase ASCII, map space/underscore/dot to dash, drop non-ascii, collapse and trim dashes.
	out := ""
	for _, r := range name {
		switch {
		case r >= 'a' && r <= 'z':
			out += string(r)
		case r >= '0' && r <= '9':
			out += string(r)
		case r >= 'A' && r <= 'Z':
			out += string(r + 32) // to lowercase
		case r == '-' || r == '_' || r == ' ' || r == '.':
			out += "-" // unify to dash
		default:
			// drop any other unicode or punctuation
		}
	}
	// collapse duplicate dashes
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
	// trim leading/trailing dashes
	for len(cleaned) > 0 && cleaned[0] == '-' {
		cleaned = cleaned[1:]
	}
	for len(cleaned) > 0 && cleaned[len(cleaned)-1] == '-' {
		cleaned = cleaned[:len(cleaned)-1]
	}
	if len(cleaned) == 0 {
		// Fallback to a safe generated name
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
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: "Failed to read body"})
		return
	}
	var req CreateOrGetChannelRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: "Invalid JSON"})
		return
	}
	if req.TeamID == "" || req.Name == "" || (req.Type != "O" && req.Type != "P") {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: "team_id, name and valid type are required"})
		return
	}

	name := normalizeChannelName(req.Name)

	// Try to get existing channel by name
	ch, appErr := p.API.GetChannelByName(name, req.TeamID, false)
	if appErr == nil && ch != nil {
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(CreateOrGetChannelResponse{ChannelID: ch.Id})
		return
	}

	// Create new channel
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
		json.NewEncoder(w).Encode(CreateOrGetChannelResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(CreateOrGetChannelResponse{ChannelID: created.Id})
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
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(AddChannelMembersResponse{Error: "Failed to read body"})
		return
	}
	var req AddChannelMembersRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(AddChannelMembersResponse{Error: "Invalid JSON"})
		return
	}
	if req.ChannelID == "" || len(req.UserIDs) == 0 {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(AddChannelMembersResponse{Error: "channel_id and user_ids are required"})
		return
	}
	added := make([]string, 0, len(req.UserIDs))
	for _, uid := range req.UserIDs {
		if uid == "" {
			continue
		}
		cm := &model.ChannelMember{ChannelId: req.ChannelID, UserId: uid}
		if _, appErr := p.API.AddChannelMember(req.ChannelID, uid); appErr != nil {
			// try to continue on error (e.g., already a member)
			p.API.LogWarn("AddChannelMember failed", "channel_id", req.ChannelID, "user_id", uid, "error", appErr.Error())
			continue
		}
		added = append(added, cm.UserId)
	}
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(AddChannelMembersResponse{Added: added})
}

type ArchiveChannelRequest struct {
	ChannelID string `json:"channel_id"`
}

type ArchiveChannelResponse struct {
	Error string `json:"error,omitempty"`
}

func (p *Plugin) ArchiveChannel(w http.ResponseWriter, r *http.Request) {
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ArchiveChannelResponse{Error: "Failed to read body"})
		return
	}
	var req ArchiveChannelRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ArchiveChannelResponse{Error: "Invalid JSON"})
		return
	}
	if req.ChannelID == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ArchiveChannelResponse{Error: "channel_id is required"})
		return
	}
	if appErr := p.API.DeleteChannel(req.ChannelID); appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(ArchiveChannelResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(ArchiveChannelResponse{})
}

type CreateDMRequest struct {
	UserIDs []string `json:"user_ids"` // must contain exactly 2
}

type CreateDMResponse struct {
	ChannelID string `json:"channel_id,omitempty"`
	Error     string `json:"error,omitempty"`
}

func (p *Plugin) CreateDirectChannel(w http.ResponseWriter, r *http.Request) {
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(CreateDMResponse{Error: "Failed to read body"})
		return
	}
	var req CreateDMRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(CreateDMResponse{Error: "Invalid JSON"})
		return
	}
	if len(req.UserIDs) != 2 {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(CreateDMResponse{Error: "user_ids must contain exactly 2 ids"})
		return
	}
	ch, appErr := p.API.GetDirectChannel(req.UserIDs[0], req.UserIDs[1])
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(CreateDMResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(CreateDMResponse{ChannelID: ch.Id})
}

type CreateGDMRequest struct {
	UserIDs []string `json:"user_ids"` // 3..8 users typically
}

type CreateGDMResponse struct {
	ChannelID string `json:"channel_id,omitempty"`
	Error     string `json:"error,omitempty"`
}

func (p *Plugin) CreateGroupChannel(w http.ResponseWriter, r *http.Request) {
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(CreateGDMResponse{Error: "Failed to read body"})
		return
	}
	var req CreateGDMRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(CreateGDMResponse{Error: "Invalid JSON"})
		return
	}
	if len(req.UserIDs) < 3 {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(CreateGDMResponse{Error: "user_ids must contain at least 3 ids"})
		return
	}
	ch, appErr := p.API.GetGroupChannel(req.UserIDs)
	if appErr != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(CreateGDMResponse{Error: appErr.Error()})
		return
	}
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(CreateGDMResponse{ChannelID: ch.Id})
}
