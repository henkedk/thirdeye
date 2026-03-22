package main

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

type DBConfig struct {
	Socket string `yaml:"socket"`
	Port   int    `yaml:"port"`
	Name   string `yaml:"name"`
	User   string `yaml:"user"`
}

type Config struct {
	Listen          string   `yaml:"listen"`
	AllowFrom       []string `yaml:"allow_from"`
	Token           string   `yaml:"token"`
	DB              DBConfig `yaml:"db"`
	PreBufferMs     int64    `yaml:"pre_buffer_ms"`
	PostBufferMs    int64    `yaml:"post_buffer_ms"`
	MaxEventsPerSec int      `yaml:"max_events_per_sec"`
	LogLevel        string   `yaml:"log_level"`
}

func LoadConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading config: %w", err)
	}

	cfg := &Config{
		Listen:          "0.0.0.0:9090",
		PreBufferMs:     2000,
		PostBufferMs:    2000,
		MaxEventsPerSec: 10,
		LogLevel:        "info",
		DB: DBConfig{
			Socket: "/run/postgresql",
			Port:   5433,
			Name:   "unifi-protect",
			User:   "postgres",
		},
	}

	if err := yaml.Unmarshal(data, cfg); err != nil {
		return nil, fmt.Errorf("parsing config: %w", err)
	}

	if cfg.Token == "" {
		return nil, fmt.Errorf("config: token is required")
	}
	if len(cfg.AllowFrom) == 0 {
		return nil, fmt.Errorf("config: allow_from requires at least one IP")
	}

	return cfg, nil
}
