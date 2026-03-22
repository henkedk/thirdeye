package main

import (
	"encoding/base64"
	"fmt"
	"regexp"
	"time"
)

var uuidRegex = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// All smart detection types supported by UniFi Protect.
// The injector accepts any of these — the bridge client decides which ones it can provide.
var validDetectTypes = map[string]bool{
	// Visual detection types
	"person":       true,
	"vehicle":      true,
	"animal":       true,
	"package":      true,
	"licensePlate": true,
	"face":         true,
	// Audio detection types
	"smoke":       true,
	"cmonx":       true,
	"bark":        true,
	"burglar":     true,
	"glass_break": true,
	"car_alarm":   true,
	"car_horn":    true,
	"speak":       true,
	"baby_cry":    true,
}

func validateUUID(s string) error {
	if !uuidRegex.MatchString(s) {
		return fmt.Errorf("invalid UUID format: %s", s)
	}
	return nil
}

func validateTimestamp(ts int64) error {
	now := time.Now().UnixMilli()
	if ts > now+60_000 {
		return fmt.Errorf("timestamp is in the future")
	}
	if ts < now-86_400_000 {
		return fmt.Errorf("timestamp is more than 24h old")
	}
	return nil
}

func validateDetectType(t string) error {
	if !validDetectTypes[t] {
		return fmt.Errorf("invalid detect type: %s", t)
	}
	return nil
}

func validateScore(score int) error {
	if score < 0 || score > 100 {
		return fmt.Errorf("score must be 0-100, got %d", score)
	}
	return nil
}

// validateThumbnail decodes base64 and checks JPEG magic bytes.
// Returns decoded bytes on success.
func validateThumbnail(b64 string) ([]byte, error) {
	if b64 == "" {
		return nil, fmt.Errorf("thumbnail is required")
	}

	data, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return nil, fmt.Errorf("thumbnail base64 decode failed: %w", err)
	}

	if len(data) < 3 {
		return nil, fmt.Errorf("thumbnail too small to be JPEG")
	}

	// JPEG magic: FF D8 FF
	if data[0] != 0xFF || data[1] != 0xD8 || data[2] != 0xFF {
		return nil, fmt.Errorf("thumbnail is not a valid JPEG (bad magic bytes)")
	}

	return data, nil
}
