package main

import (
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net"
	"net/http"
	"sync"
	"time"
)

type Server struct {
	db     *DB
	cfg    *Config
	logger *slog.Logger

	// Rate limiting: per-camera sliding window
	rateMu    sync.Mutex
	rateCount map[string][]time.Time
}

func NewServer(db *DB, cfg *Config, logger *slog.Logger) *Server {
	return &Server{
		db:        db,
		cfg:       cfg,
		logger:    logger,
		rateCount: make(map[string][]time.Time),
	}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /event/start", s.handleEventStart)
	mux.HandleFunc("POST /event/end", s.handleEventEnd)
	mux.HandleFunc("GET /cameras", s.handleCameras)
	mux.HandleFunc("GET /health", s.handleHealth)
	return s.middlewareIPAllow(s.middlewareAuth(mux))
}

// middlewareIPAllow checks the request source IP against the allowlist.
func (s *Server) middlewareIPAllow(next http.Handler) http.Handler {
	allowed := make(map[string]bool, len(s.cfg.AllowFrom))
	for _, ip := range s.cfg.AllowFrom {
		allowed[ip] = true
	}

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host, _, err := net.SplitHostPort(r.RemoteAddr)
		if err != nil {
			host = r.RemoteAddr
		}
		if !allowed[host] {
			s.logger.Warn("blocked request from disallowed IP", "ip", host)
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// middlewareAuth checks the X-Bridge-Token header using constant-time comparison.
func (s *Server) middlewareAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Health endpoint is unauthenticated
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}

		token := r.Header.Get("X-Bridge-Token")
		if subtle.ConstantTimeCompare([]byte(token), []byte(s.cfg.Token)) != 1 {
			s.logger.Warn("invalid token", "ip", r.RemoteAddr)
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// checkRateLimit returns true if the camera has exceeded max_events_per_sec.
func (s *Server) checkRateLimit(cameraID string) bool {
	s.rateMu.Lock()
	defer s.rateMu.Unlock()

	now := time.Now()
	cutoff := now.Add(-time.Second)

	// Prune old entries
	events := s.rateCount[cameraID]
	start := 0
	for start < len(events) && events[start].Before(cutoff) {
		start++
	}
	events = events[start:]

	if len(events) >= s.cfg.MaxEventsPerSec {
		s.rateCount[cameraID] = events
		return true
	}

	s.rateCount[cameraID] = append(events, now)
	return false
}

type eventStartRequest struct {
	CameraID  string `json:"cameraId"`
	Type      string `json:"type"`
	Timestamp int64  `json:"timestamp"`
	Score     int    `json:"score"`
	Thumbnail string `json:"thumbnail"`
}

func (s *Server) handleEventStart(w http.ResponseWriter, r *http.Request) {
	var req eventStartRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	// Validate all fields
	if err := validateUUID(req.CameraID); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if err := validateDetectType(req.Type); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if err := validateTimestamp(req.Timestamp); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if err := validateScore(req.Score); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	thumbnailData, err := validateThumbnail(req.Thumbnail)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}

	// Check camera exists
	exists, err := s.db.CameraExists(req.CameraID)
	if err != nil {
		s.logger.Error("checking camera existence", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "database error"})
		return
	}
	if !exists {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "camera not found: " + req.CameraID})
		return
	}

	// Rate limit
	if s.checkRateLimit(req.CameraID) {
		s.logger.Warn("rate limit exceeded", "cameraId", req.CameraID)
		writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "rate limit exceeded"})
		return
	}

	result, err := s.db.InsertEvent(req.CameraID, req.Type, req.Timestamp, req.Score, thumbnailData, s.cfg.PreBufferMs, s.cfg.PostBufferMs)
	if err != nil {
		s.logger.Error("inserting event", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "database error"})
		return
	}

	s.logger.Info("event started", "eventId", result.EventID, "cameraId", req.CameraID, "type", req.Type)
	writeJSON(w, http.StatusOK, result)
}

type eventEndRequest struct {
	EventID   string `json:"eventId"`
	Timestamp int64  `json:"timestamp"`
}

func (s *Server) handleEventEnd(w http.ResponseWriter, r *http.Request) {
	var req eventEndRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	if err := validateUUID(req.EventID); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if err := validateTimestamp(req.Timestamp); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}

	if err := s.db.UpdateEventEnd(req.EventID, req.Timestamp, s.cfg.PostBufferMs); err != nil {
		s.logger.Error("updating event end", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	s.logger.Info("event ended", "eventId", req.EventID)
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleCameras(w http.ResponseWriter, r *http.Request) {
	cameras, err := s.db.ListCameras()
	if err != nil {
		s.logger.Error("listing cameras", "error", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "database error"})
		return
	}
	if cameras == nil {
		cameras = []Camera{}
	}
	writeJSON(w, http.StatusOK, cameras)
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	status := "ok"
	schemaOK := true

	if err := s.db.Raw().Ping(); err != nil {
		status = "db_unreachable"
		schemaOK = false
	}

	fingerprint := ""
	if schemaOK {
		fp, err := SchemaFingerprint(s.db.Raw())
		if err != nil {
			status = "schema_error"
			schemaOK = false
		} else {
			fingerprint = fp
		}
	}

	resp := map[string]interface{}{
		"status":            status,
		"schemaValid":       schemaOK,
		"schemaFingerprint": fingerprint,
		"version":           Version,
	}
	writeJSON(w, http.StatusOK, resp)
}

func writeJSON(w http.ResponseWriter, code int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}
