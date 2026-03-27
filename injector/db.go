package main

import (
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"fmt"
	"time"

	_ "github.com/lib/pq"
)

type DB struct {
	conn *sql.DB
}

func NewDB(cfg DBConfig) (*DB, error) {
	dsn := fmt.Sprintf("host=%s port=%d dbname=%s user=%s sslmode=disable",
		cfg.Socket, cfg.Port, cfg.Name, cfg.User)

	conn, err := sql.Open("postgres", dsn)
	if err != nil {
		return nil, fmt.Errorf("opening database: %w", err)
	}

	conn.SetMaxOpenConns(5)
	conn.SetMaxIdleConns(2)
	conn.SetConnMaxLifetime(5 * time.Minute)

	if err := conn.Ping(); err != nil {
		return nil, fmt.Errorf("pinging database: %w", err)
	}

	return &DB{conn: conn}, nil
}

func (d *DB) Close() error {
	return d.conn.Close()
}

func (d *DB) Raw() *sql.DB {
	return d.conn
}

// generate24HexID generates a 24-char hex string (12 random bytes).
// This length is critical: Protect routes 24-char thumbnailIds to the local thumbnails table.
func generate24HexID() (string, error) {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

type EventStartResult struct {
	EventID             string `json:"eventId"`
	SmartDetectObjectID string `json:"smartDetectObjectId"`
}

// InsertEvent creates an event, smartDetectObject, and thumbnail in a single transaction.
func (d *DB) InsertEvent(cameraID, detectType string, timestamp int64, score int, thumbnailData []byte, preBufferMs, postBufferMs int64) (*EventStartResult, error) {
	eventID := newUUID()
	sdoID := newUUID()
	thumbnailID, err := generate24HexID()
	if err != nil {
		return nil, fmt.Errorf("generating thumbnailId: %w", err)
	}

	now := time.Now().UTC().Format(time.RFC3339)
	adjustedStart := timestamp - preBufferMs

	smartDetectTypes := fmt.Sprintf(`["%s"]`, detectType)
	metadata := `{"source": "thirdeye", "version": "1.0.0"}`
	attributes := fmt.Sprintf(`{"confidence": %d}`, score)

	tx, err := d.conn.Begin()
	if err != nil {
		return nil, fmt.Errorf("beginning transaction: %w", err)
	}
	defer tx.Rollback()

	// Insert event
	_, err = tx.Exec(`INSERT INTO events (id, type, start, "cameraId", score, "smartDetectTypes",
		metadata, locked, "thumbnailId", "createdAt", "updatedAt")
		VALUES ($1, 'smartDetectZone', $2, $3, $4, $5::json,
		$6::json, false, $7, $8, $9)`,
		eventID, adjustedStart, cameraID, score, smartDetectTypes,
		metadata, thumbnailID, now, now)
	if err != nil {
		return nil, fmt.Errorf("inserting event: %w", err)
	}

	// Insert smartDetectObject
	_, err = tx.Exec(`INSERT INTO "smartDetectObjects" (id, "eventId", "thumbnailId", "cameraId",
		type, attributes, "detectedAt", metadata, "createdAt", "updatedAt")
		VALUES ($1, $2, $3, $4, $5, $6::json, $7, '{}'::json, $8, $9)`,
		sdoID, eventID, thumbnailID, cameraID, detectType, attributes,
		timestamp, now, now)
	if err != nil {
		return nil, fmt.Errorf("inserting smartDetectObject: %w", err)
	}

	// Insert thumbnail
	_, err = tx.Exec(`INSERT INTO thumbnails (id, "eventId", "cameraId", "createdAt", content)
		VALUES ($1, $2, $3, $4, $5)`,
		thumbnailID, eventID, cameraID, now, thumbnailData)
	if err != nil {
		return nil, fmt.Errorf("inserting thumbnail: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing transaction: %w", err)
	}

	return &EventStartResult{
		EventID:             eventID,
		SmartDetectObjectID: sdoID,
	}, nil
}

// UpdateEventEnd sets the end timestamp on an event.
func (d *DB) UpdateEventEnd(eventID string, timestamp int64, postBufferMs int64) error {
	adjustedEnd := timestamp + postBufferMs
	now := time.Now().UTC().Format(time.RFC3339)

	result, err := d.conn.Exec(`UPDATE events SET "end" = $1, "updatedAt" = $2 WHERE id = $3`,
		adjustedEnd, now, eventID)
	if err != nil {
		return fmt.Errorf("updating event end: %w", err)
	}

	rows, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("checking rows affected: %w", err)
	}
	if rows == 0 {
		return fmt.Errorf("event not found: %s", eventID)
	}
	return nil
}

type Camera struct {
	ID                  string `json:"id"`
	MAC                 string `json:"mac"`
	Host                string `json:"host"`
	ThirdPartyCameraInfo string `json:"thirdPartyCameraInfo,omitempty"`
}

// ListCameras returns all adopted third-party cameras.
func (d *DB) ListCameras() ([]Camera, error) {
	rows, err := d.conn.Query(`SELECT id, mac, host, "thirdPartyCameraInfo"
		FROM cameras
		WHERE "isThirdPartyCamera" = true AND "isAdopted" = true AND host IS NOT NULL`)
	if err != nil {
		return nil, fmt.Errorf("querying cameras: %w", err)
	}
	defer rows.Close()

	var cameras []Camera
	for rows.Next() {
		var c Camera
		var tpInfo sql.NullString
		if err := rows.Scan(&c.ID, &c.MAC, &c.Host, &tpInfo); err != nil {
			return nil, fmt.Errorf("scanning camera: %w", err)
		}
		if tpInfo.Valid {
			c.ThirdPartyCameraInfo = tpInfo.String
		}
		cameras = append(cameras, c)
	}
	return cameras, rows.Err()
}

// CameraExists checks if a camera ID exists in the adopted third-party cameras.
func (d *DB) CameraExists(id string) (bool, error) {
	var exists bool
	err := d.conn.QueryRow(`SELECT EXISTS(
		SELECT 1 FROM cameras WHERE id = $1 AND "isThirdPartyCamera" = true AND "isAdopted" = true
	)`, id).Scan(&exists)
	return exists, err
}

// EnableSmartDetect patches featureFlags and smartDetectSettings on all adopted third-party cameras
// so Protect UI shows smart detection filters for all supported visual types.
func (d *DB) EnableSmartDetect() (int64, error) {
	result, err := d.conn.Exec(`UPDATE cameras
		SET "featureFlags" = jsonb_set(
			COALESCE("featureFlags"::jsonb, '{}'::jsonb),
			'{smartDetectTypes}',
			'["person","vehicle","animal","package","licensePlate","face"]'::jsonb
		),
		"smartDetectSettings" = jsonb_set(
			COALESCE("smartDetectSettings"::jsonb, '{}'::jsonb),
			'{objectTypes}',
			'["person","vehicle","animal","package","licensePlate","face"]'::jsonb
		),
		"updatedAt" = $1
		WHERE "isThirdPartyCamera" = true AND "isAdopted" = true AND host IS NOT NULL`,
		time.Now().UTC().Format(time.RFC3339))
	if err != nil {
		return 0, fmt.Errorf("enabling smart detect: %w", err)
	}
	return result.RowsAffected()
}

func newUUID() string {
	b := make([]byte, 16)
	rand.Read(b)
	b[6] = (b[6] & 0x0f) | 0x40 // v4
	b[8] = (b[8] & 0x3f) | 0x80 // variant
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}
