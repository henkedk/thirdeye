package main

import (
	"crypto/sha256"
	"database/sql"
	"fmt"
	"sort"
	"strings"
)

// requiredTables lists the tables we need and their expected columns.
var requiredTables = map[string][]string{
	"events": {
		"id", "type", "start", "end", "cameraId", "score",
		"smartDetectTypes", "metadata", "locked", "thumbnailId",
		"createdAt", "updatedAt",
	},
	"smartDetectObjects": {
		"id", "eventId", "thumbnailId", "cameraId",
		"type", "attributes", "detectedAt", "metadata",
		"createdAt", "updatedAt",
	},
	"thumbnails": {
		"id", "eventId", "cameraId", "createdAt", "data",
	},
	"cameras": {
		"id", "mac", "host", "isThirdPartyCamera", "isAdopted",
		"thirdPartyCameraInfo", "featureFlags", "smartDetectSettings",
	},
}

// SchemaFingerprint queries information_schema.columns for relevant tables
// and returns a SHA-256 hex digest of the sorted column names.
func SchemaFingerprint(db *sql.DB) (string, error) {
	tables := make([]string, 0, len(requiredTables))
	for t := range requiredTables {
		tables = append(tables, t)
	}
	sort.Strings(tables)

	var parts []string

	for _, table := range tables {
		rows, err := db.Query(
			`SELECT column_name FROM information_schema.columns
			 WHERE table_schema = 'public' AND table_name = $1
			 ORDER BY column_name`, table)
		if err != nil {
			return "", fmt.Errorf("querying schema for %s: %w", table, err)
		}

		var cols []string
		for rows.Next() {
			var col string
			if err := rows.Scan(&col); err != nil {
				rows.Close()
				return "", fmt.Errorf("scanning column for %s: %w", table, err)
			}
			cols = append(cols, col)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return "", fmt.Errorf("iterating columns for %s: %w", table, err)
		}

		if len(cols) == 0 {
			return "", fmt.Errorf("table %q not found in schema", table)
		}

		parts = append(parts, fmt.Sprintf("%s:%s", table, strings.Join(cols, ",")))
	}

	h := sha256.Sum256([]byte(strings.Join(parts, "|")))
	return fmt.Sprintf("%x", h), nil
}

// ValidateSchema checks that all required columns exist in each table.
func ValidateSchema(db *sql.DB) error {
	for table, expectedCols := range requiredTables {
		rows, err := db.Query(
			`SELECT column_name FROM information_schema.columns
			 WHERE table_schema = 'public' AND table_name = $1`, table)
		if err != nil {
			return fmt.Errorf("querying schema for %s: %w", table, err)
		}

		actual := make(map[string]bool)
		for rows.Next() {
			var col string
			if err := rows.Scan(&col); err != nil {
				rows.Close()
				return fmt.Errorf("scanning column for %s: %w", table, err)
			}
			actual[col] = true
		}
		rows.Close()

		if len(actual) == 0 {
			return fmt.Errorf("table %q not found", table)
		}

		for _, col := range expectedCols {
			if !actual[col] {
				return fmt.Errorf("table %q missing required column %q", table, col)
			}
		}
	}
	return nil
}
