package main

import (
	"context"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

const Version = "1.0.0"

func main() {
	configPath := flag.String("config", "/data/thirdeye-injector/config.yaml", "path to config file")
	flag.Parse()

	cfg, err := LoadConfig(*configPath)
	if err != nil {
		slog.Error("failed to load config", "error", err)
		os.Exit(1)
	}

	// Set up structured logger
	level := slog.LevelInfo
	switch cfg.LogLevel {
	case "debug":
		level = slog.LevelDebug
	case "warn":
		level = slog.LevelWarn
	case "error":
		level = slog.LevelError
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
	slog.SetDefault(logger)

	logger.Info("starting thirdeye-injector", "version", Version, "listen", cfg.Listen)

	// Connect to database
	db, err := NewDB(cfg.DB)
	if err != nil {
		logger.Error("failed to connect to database", "error", err)
		os.Exit(1)
	}
	defer db.Close()

	// Validate schema on startup
	if err := ValidateSchema(db.Raw()); err != nil {
		logger.Error("schema validation failed — aborting", "error", err)
		os.Exit(1)
	}

	fp, err := SchemaFingerprint(db.Raw())
	if err != nil {
		logger.Error("schema fingerprint failed", "error", err)
		os.Exit(1)
	}
	logger.Info("schema validated", "fingerprint", fp)

	// Enable smart detect on all adopted third-party cameras
	updated, err := db.EnableSmartDetect()
	if err != nil {
		logger.Error("failed to enable smart detect on cameras", "error", err)
		os.Exit(1)
	}
	logger.Info("enabled smart detect on cameras", "count", updated)

	// Create server
	srv := NewServer(db, cfg, logger)
	httpServer := &http.Server{
		Addr:         cfg.Listen,
		Handler:      srv.Handler(),
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Graceful shutdown
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	go func() {
		logger.Info("listening", "addr", cfg.Listen)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("server error", "error", err)
			os.Exit(1)
		}
	}()

	<-ctx.Done()
	logger.Info("shutting down")

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		logger.Error("shutdown error", "error", err)
	}
	logger.Info("stopped")
}
