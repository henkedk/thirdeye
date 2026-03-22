# Spec: thirdeye

## What
Monorepo containing two components that bridge Reolink camera AI smart detections into UniFi Protect's native UI:

1. **thirdeye-bridge** (Python) — Connects to Reolink cameras via Baichuan TCP, receives AI detection events, captures snapshots, and pushes structured payloads to the injector.
2. **thirdeye-injector** (Go) — Static binary running on the UDM. Exposes a locked-down HTTP API that validates and writes events to Protect's PostgreSQL database.

## Why
UniFi Protect supports adopting third-party cameras via ONVIF, but Reolink cameras don't expose their AI detections through ONVIF — only basic motion. This bridge taps into Reolink's proprietary Baichuan protocol (which carries person/vehicle/animal detections) and injects them into Protect's DB so they appear as native smart detection events.

## Architecture

```
┌──────────────┐    Baichuan TCP     ┌─────────────────────┐
│ Reolink Cams │◄───────────────────►│  thirdeye-bridge     │
│ (N cameras)  │    push events      │  (Python, external) │
└──────────────┘                     └──────────┬──────────┘
                                                │ HTTP POST
                                                │ (IP-locked + token)
                                     ┌──────────▼──────────┐
                                     │  thirdeye-injector   │
                                     │  (Go static binary, │
                                     │   on UDM)           │
                                     └──────────┬──────────┘
                                                │ Unix socket / localhost
                                     ┌──────────▼──────────┐
                                     │  Protect PostgreSQL  │
                                     │  (port 5433)         │
                                     └─────────────────────┘
```

## Component 1: thirdeye-injector (Go)

### Purpose
Validated DB write proxy. No camera logic, no protocol handling. Accepts structured event payloads, validates them, writes to Protect's PostgreSQL. ~500 lines of Go.

### API (4 endpoints, nothing else)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/event/start` | Create event + smartDetectObject + thumbnail |
| `POST` | `/event/end` | Update event end timestamp |
| `GET` | `/cameras` | List adopted third-party cameras (IDs, MACs, IPs) |
| `GET` | `/health` | Status + schema fingerprint + Protect version |

### POST /event/start payload
```json
{
  "cameraId": "protect-uuid-here",
  "type": "person",
  "timestamp": 1711094400000,
  "score": 85,
  "thumbnail": "<base64-encoded-jpeg>"
}
```

**Supported `type` values** — the injector accepts all Protect smart detection types:

| Category | Types |
|----------|-------|
| Visual | `person`, `vehicle`, `animal`, `package`, `licensePlate`, `face` |
| Audio | `smoke`, `cmonx`, `bark`, `burglar`, `glass_break`, `car_alarm`, `car_horn`, `speak`, `baby_cry` |

The injector is type-agnostic — it validates the type exists in Protect's vocabulary and passes it through. Bridge clients decide which types they can supply based on their camera/AI capabilities.

Response: `{"eventId": "uuid", "smartDetectObjectId": "uuid"}`

### POST /event/end payload
```json
{
  "eventId": "uuid",
  "timestamp": 1711094410000
}
```

### Security layers

1. **IP allowlist** — `--allow-from 192.168.1.50` (configurable, multiple IPs supported). Connections from other IPs get TCP RST.
2. **Shared token** — `X-Bridge-Token` header. Generated at setup, stored in config on both sides. Constant-time comparison.
3. **Schema fingerprinting** — On startup, queries `information_schema.columns` for events/smartDetectObjects/thumbnails. Compares against known-good fingerprint. Hard abort if mismatch.
4. **Write validation** — Every insert: UUID format check, timestamp sanity (not future, not >24h old), cameraId must exist in cameras table, JPEG magic bytes check on thumbnail.
5. **Rate limiting** — Max events/sec per camera (configurable, default 10/sec). Circuit-breaker per camera if exceeded.
6. **Event tagging** — All events get metadata: `{"source": "thirdeye", "version": "1.0.0"}`. Enables clean removal and audit.
7. **Transaction wrapping** — event + smartDetectObject + thumbnail inserted in single transaction. All or nothing.

### DB operations (the only 3 SQL patterns the binary executes)

**Insert event + SDO + thumbnail (one transaction):**
```sql
BEGIN;
INSERT INTO events (id, type, start, "cameraId", score, "smartDetectTypes",
  metadata, locked, "thumbnailId", "createdAt", "updatedAt")
VALUES ($1, 'smartDetectZone', $2, $3, $4, $5::json,
  $6::json, false, $7, $8, $9);

INSERT INTO "smartDetectObjects" (id, "eventId", "thumbnailId", "cameraId",
  type, attributes, "detectedAt", metadata, "createdAt", "updatedAt")
VALUES ($1, $2, $3, $4, $5, $6, $7, '{}'::json, $8, $9);

INSERT INTO thumbnails (id, "eventId", "cameraId", "createdAt", data)
VALUES ($1, $2, $3, $4, $5);
COMMIT;
```

**Update event end:**
```sql
UPDATE events SET "end" = $1, "updatedAt" = $2 WHERE id = $3;
```

**Read cameras:**
```sql
SELECT id, mac, host, "thirdPartyCameraInfo"
FROM cameras
WHERE "isThirdPartyCamera" = true AND "isAdopted" = true AND host IS NOT NULL;
```

### Build
- Go, static binary, cross-compiled to ARM64 (UDM Pro SE) + x86_64
- Zero runtime dependencies
- Ships as GitHub release artifact

### Deployment on UDM (`/data/` persistence)
All files live in `/data/thirdeye-injector/` which survives firmware updates:
```
/data/thirdeye-injector/
├── thirdeye-injector          # binary
├── config.yaml               # config
└── thirdeye-injector.service  # systemd unit
```

Install:
```bash
scp thirdeye-injector root@<udm>:/data/thirdeye-injector/
scp thirdeye-injector.service root@<udm>:/data/thirdeye-injector/
scp config.yaml root@<udm>:/data/thirdeye-injector/
ssh root@<udm> 'ln -sf /data/thirdeye-injector/thirdeye-injector.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now thirdeye-injector'
```

### Boot hook (auto-restore after firmware update)
The symlink in `/etc/systemd/system/` may get wiped on firmware update, but the binary and config in `/data/` survive. A boot hook restores it automatically:

```bash
#!/bin/bash
# /data/on_boot.d/10-thirdeye-injector.sh
ln -sf /data/thirdeye-injector/thirdeye-injector.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now thirdeye-injector
```

`/data/on_boot.d/` is a community convention (used by udm-utilities and others), not officially supported by Ubiquiti. Works on current UniFi OS versions. The manual reinstall path (just re-symlink) is documented as fallback.

### Config (`/data/thirdeye-injector/config.yaml`)
```yaml
listen: "0.0.0.0:9090"
allow_from:
  - "192.168.1.50"
token: "generated-secret-here"
db:
  socket: "/run/postgresql"
  port: 5433
  name: "unifi-protect"
  user: "postgres"
pre_buffer_ms: 2000
post_buffer_ms: 2000
max_events_per_sec: 10
log_level: "info"
```

## Component 2: thirdeye-bridge (Python)

### Purpose
Camera connectivity and event classification. Connects to Reolink cameras via Baichuan TCP, receives AI detections, captures JPEG snapshots, and pushes to the injector API.

### Modules

| File | Purpose |
|------|---------|
| `bridge.py` | Main entry point, asyncio event loop, graceful shutdown |
| `camera_manager.py` | Baichuan connections via reolink_aio, auto-reconnect, auto-discovery, hot-add/remove |
| `classifier.py` | Maps Reolink AI types → Protect types. Debounce (merge within 2s). Detection start/end tracking |
| `snapshot.py` | Fetches JPEG from camera HTTP API (`/api.cgi?cmd=Snap`) on detection start |
| `injector_client.py` | HTTP client for thirdeye-injector API. Retry logic, health checks |
| `config.py` | Config loading, env var expansion for secrets |

### Camera Auto-Discovery
No per-camera config needed. The bridge discovers cameras automatically:

1. **Startup:** Calls `GET /cameras` on injector → gets all adopted third-party cameras (IPs, MACs, UUIDs)
2. **Connect:** Opens Baichuan TCP to each camera using default credentials
3. **Poll:** Re-checks `GET /cameras` every 60s (configurable)
4. **Hot-add:** New camera adopted into Protect → bridge detects it next poll → connects automatically
5. **Hot-remove:** Camera unadopted/offline → bridge closes connection, stops events

Only default credentials needed in config. Per-camera overrides available if needed.

### Detection type mapping
| Reolink (Baichuan) | Protect (smartDetectObject) |
|---------------------|-----------------------------|
| `people` | `person` |
| `vehicle` | `vehicle` |
| `dog_cat` | `animal` (if Protect supports, else skip) |

### Config (`config.yaml`)
```yaml
injector:
  url: "http://192.168.1.1:9090"
  token: "${BRIDGE_TOKEN}"

camera_defaults:
  username: "python"
  password: "${REOLINK_PASSWORD}"

# Optional: override credentials for specific cameras
camera_overrides:
  "192.168.1.151":
    username: "different_user"
    password: "different_pass"

discovery:
  poll_interval_sec: 60

detection:
  debounce_sec: 2
  snapshot_on_detect: true

logging:
  level: INFO
  file: /var/log/thirdeye-bridge.log
```

### Deployment
- `pip install .` or `docker compose up`
- Runs on any Linux host on the LAN (Proxmox, NAS, Pi, etc.)

## Monorepo Structure
```
thirdeye/
├── README.md
├── LICENSE (MIT)
├── SPEC.md
├── CLAUDE.md
│
├── injector/                     # Go - thirdeye-injector
│   ├── main.go
│   ├── handler.go                # HTTP handlers
│   ├── db.go                     # PostgreSQL operations
│   ├── validate.go               # Input validation
│   ├── schema.go                 # Schema fingerprinting
│   ├── config.go                 # Config loading
│   ├── go.mod
│   ├── go.sum
│   └── Makefile                  # Cross-compile targets
│
├── bridge/                       # Python - thirdeye-bridge
│   ├── pyproject.toml
│   ├── src/
│   │   └── thirdeye_bridge/
│   │       ├── __init__.py
│   │       ├── bridge.py
│   │       ├── camera_manager.py
│   │       ├── classifier.py
│   │       ├── snapshot.py
│   │       ├── injector_client.py
│   │       └── config.py
│   └── tests/
│
├── systemd/
│   ├── thirdeye-injector.service
│   └── thirdeye-bridge.service
│
├── docker/
│   └── Dockerfile                # Python bridge only
│
├── scripts/
│   ├── setup-injector.sh         # One-liner UDM install
│   └── cleanup.py                # Remove all bridge-injected data
│
└── tests/
    └── integration/              # End-to-end with mock camera + DB
```

## Cleanup / Rollback
`scripts/cleanup.py` connects to injector's API or directly to PG and:
- Counts bridge-injected events (via metadata tag)
- Removes all tagged events, smartDetectObjects, thumbnails
- Reverts featureFlags/smartDetectSettings patches on cameras
- Complete undo — as if the bridge never existed

## Protect Schema Reference (from danielwoz reverse-engineering)

### events table
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT (UUID) | PK |
| type | TEXT | Always `smartDetectZone` |
| start | BIGINT | ms since epoch |
| end | BIGINT | ms since epoch, NULL while active |
| cameraId | TEXT | Protect camera UUID |
| score | INTEGER | Detection confidence (0-100) |
| smartDetectTypes | JSON | `["person"]` or `["vehicle"]` |
| metadata | JSON | We tag with source info |
| locked | BOOLEAN | false |
| thumbnailId | TEXT | 24-char hex → routes to thumbnails table |
| createdAt | TEXT | ISO-8601 UTC |
| updatedAt | TEXT | ISO-8601 UTC |

### smartDetectObjects table
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT (UUID) | PK |
| eventId | TEXT | FK → events.id |
| thumbnailId | TEXT | Same as event thumbnailId |
| cameraId | TEXT | Protect camera UUID |
| type | TEXT | `person` / `vehicle` |
| attributes | JSON | `{"confidence": N}` |
| detectedAt | BIGINT | ms since epoch |
| metadata | JSON | `{}` |
| createdAt | TEXT | ISO-8601 |
| updatedAt | TEXT | ISO-8601 |

### thumbnails table
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT | 24-char hex (critical: this length routes to local DB) |
| eventId | TEXT | FK → events.id |
| cameraId | TEXT | Protect camera UUID |
| createdAt | TEXT | ISO-8601 |
| data | BYTEA | Raw JPEG bytes |

### cameras table (read-only)
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT (UUID) | Protect camera UUID |
| mac | TEXT | Camera MAC |
| host | TEXT | Camera IP |
| isThirdPartyCamera | BOOLEAN | Must be true |
| isAdopted | BOOLEAN | Must be true |
| thirdPartyCameraInfo | JSON | Contains username, password, snapshotUrl |
| featureFlags | JSON | Contains smartDetectTypes array |
| smartDetectSettings | JSON | Contains objectTypes array |

### Key insight: thumbnailId routing
Protect routes thumbnailId values with length == 24 to its local `thumbnails` table. Other lengths go to the MSP media server. Using `generate_24hex_id()` (12 random bytes as hex) ensures Protect serves our inserted thumbnails directly.

### Smart detect enablement
Third-party cameras need `featureFlags.smartDetectTypes` and `smartDetectSettings.objectTypes` set to `["person","vehicle"]` for Protect UI to show smart detection filters. The injector handles this on startup for all adopted cameras.

## UBV Thumbnail Format (NOT needed with PG thumbnails table approach)
The PG backend inserts JPEG directly into the `thumbnails` table. UBV files are only needed for the SQLite/file-based approach that danielwoz uses. We skip this entirely.

## What's NOT in scope (v1)
- Web UI / dashboard
- Home Assistant integration (standalone)
- Recording management (Protect handles this)
- Multiple Protect instances
- NVR support (direct camera connections only)
- Smart detection zones / line crossing (v2 candidates)

## License
**MIT** — see LICENSE file.

### Dependency & attribution analysis

| Project | License | Relationship | Obligation |
|---------|---------|-------------|------------|
| danielwoz/ubiquiti-protect-onvif-event-listener | Apache 2.0 | Knowledge reference only. No code copied. DB schema facts (table/column names) are not copyrightable. Our Go injector is an independent implementation. | Credit in README (courtesy). |
| starkillerOG/reolink_aio | MIT | Runtime pip dependency. Standard library usage. | Include copyright notice in LICENSE/NOTICES. |
| QuantumEntangledAndy/neolink | AGPL-3.0 | **No code used, no dependency.** Credited for original Baichuan protocol research only. reolink_aio is a separate MIT implementation. | Credit in README (courtesy). No AGPL obligations. |

**Interoperability disclaimer:** This project writes to UniFi Protect's PostgreSQL database for interoperability purposes. Reverse-engineering for interoperability is protected under EU Directive 2009/24/EC (Article 6) and US DMCA §1201(f). This project is not affiliated with or endorsed by Ubiquiti Inc.

## Credits
- [danielwoz/ubiquiti-protect-onvif-event-listener](https://github.com/danielwoz/ubiquiti-protect-onvif-event-listener) (Apache 2.0) — Protect DB schema research, thumbnailId routing discovery, smartDetectObject structure
- [starkillerOG/reolink_aio](https://github.com/starkillerOG/reolink_aio) (MIT) — Reolink Baichuan protocol implementation
- [QuantumEntangledAndy/neolink](https://github.com/QuantumEntangledAndy/neolink) (AGPL-3.0) — Original Baichuan protocol research (no code used)
