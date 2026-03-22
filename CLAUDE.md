# CLAUDE.md — thirdeye

## What
Monorepo bridging Reolink camera AI smart detections (person/vehicle/animal) into UniFi Protect's native timeline. Two components:

1. **thirdeye-injector** (Go) — Static binary on UDM. Locked-down HTTP API → local PostgreSQL writes.
2. **thirdeye-bridge** (Python) — External host. Baichuan TCP camera connections → pushes events to injector.

## Architecture
```
Reolink Cams --[Baichuan TCP]--> thirdeye-bridge (Python, external)
                                        |
                                  HTTP POST (IP-locked + token)
                                        |
                                        v
                                 thirdeye-injector (Go, on UDM)
                                        |
                                  Unix socket / localhost
                                        v
                                 Protect PostgreSQL (port 5433)
```

## Monorepo Structure
```
thirdeye/
├── SPEC.md                       # Full specification
├── CLAUDE.md                     # This file
│
├── injector/                     # Go — thirdeye-injector
│   ├── main.go                   # Entry point, config, server
│   ├── handler.go                # HTTP handlers (4 endpoints)
│   ├── db.go                     # PostgreSQL operations (3 SQL patterns)
│   ├── validate.go               # Input validation
│   ├── schema.go                 # Schema fingerprinting
│   ├── config.go                 # YAML config loading
│   ├── go.mod
│   └── Makefile                  # ARM64 + x86_64 cross-compile
│
├── bridge/                       # Python — thirdeye-bridge
│   ├── pyproject.toml
│   ├── src/thirdeye_bridge/
│   │   ├── bridge.py             # Main entry + asyncio loop
│   │   ├── camera_manager.py     # Baichuan connections + reconnect
│   │   ├── classifier.py         # Reolink AI → Protect types + debounce
│   │   ├── snapshot.py           # JPEG capture from camera HTTP API
│   │   ├── injector_client.py    # HTTP client for injector API
│   │   └── config.py             # YAML config + env var expansion
│   └── tests/
│
├── systemd/                      # Service files for both components
├── docker/                       # Dockerfile for Python bridge only
├── scripts/
│   ├── setup-injector.sh         # One-liner UDM install
│   └── cleanup.py                # Remove all bridge-injected data
└── tests/integration/            # E2E with mock camera + DB
```

## Key Decisions
- **Split architecture:** Python bridge (external) + Go injector (on UDM). No SSH tunnel needed.
- **Baichuan TCP** (not ONVIF) — Reolink doesn't expose AI detections via ONVIF
- **Dual Baichuan connections** confirmed working (HA + bridge coexist)
- **Injector security:** IP allowlist + shared token + schema fingerprinting + rate limiting + event tagging
- **Thumbnails:** JPEG sent as base64 in /event/start payload, injector writes to PG `thumbnails` table using 24-char hex ID (Protect routes these to local DB)
- **Event tagging:** All injected events tagged in metadata for clean rollback

## Injector API
| Method | Path | Purpose |
|--------|------|---------|
| POST | /event/start | Create event + SDO + thumbnail |
| POST | /event/end | Update event end timestamp |
| GET | /cameras | List adopted third-party cameras |
| GET | /health | Status + schema check |

## Dev Commands
```bash
# Injector (Go)
cd injector && go build -o thirdeye-injector .
GOOS=linux GOARCH=arm64 go build -o thirdeye-injector-arm64 .

# Bridge (Python)
cd bridge && pip install -e .
python -m thirdeye_bridge
```

## Dependencies
### Injector (Go)
- `lib/pq` (PostgreSQL driver)
- `gopkg.in/yaml.v3` (config)
- Standard library for HTTP server

### Bridge (Python)
- `reolink-aio` (Baichuan protocol)
- `httpx` or `aiohttp` (HTTP client for injector)
- `pyyaml` (config)
- `Pillow` (optional, thumbnail manipulation)

## Credits
- danielwoz/ubiquiti-protect-onvif-event-listener — Protect DB schema, thumbnailId routing, smartDetectObject structure
- starkillerOG/reolink_aio — Baichuan protocol
- QuantumEntangledAndy/neolink — Original Baichuan research
