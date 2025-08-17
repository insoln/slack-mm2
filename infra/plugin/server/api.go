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
