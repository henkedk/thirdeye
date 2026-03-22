# Spec: reolink-protect-bridge

## What
Python service that bridges Reolink camera AI smart detections into UniFi Protect's native UI. Connects to Reolink cameras via the proprietary Baichuan TCP protocol, receives real-time person/vehicle/animal detection events, and writes them to Protect's PostgreSQL database so they appear as native smart detection events in the Protect timeline.

## Why
UniFi Protect supports adopting third-party cameras via ONVIF, but Reolink cameras don't expose their AI detections through ONVIF — only basic motion. This means adopted Reolink cameras show up in Protect without smart detection (person/vehicle filtering). This bridge solves that by tapping into Reolink's proprietary Baichuan protocol (which does carry AI detections) and writing them directly to Protect's DB.

## Architecture

```
┌──────────────┐    Baichuan TCP     ┌─────────────────────┐
│ Reolink Cams │◄───────────────────►│  reolink-protect    │
│ (11 cameras) │    push events      │  bridge             │
└──────────────┘                     │  (Python, asyncio)  │
                                     └──────────┬──────────┘
                                                │ PostgreSQL
                                                │ (SSH tunnel or direct)
                                     ┌──────────▼──────────┐
                                     │  UniFi Protect DB   │
                                     │  (UDM Pro SE)       │
                                     └─────────────────────┘
```

- Bridge runs on any Linux host on the LAN (Proxmox VM, Docker, etc.)
- Cameras accept multiple Baichuan connections (verified — HA integration coexists fine)
- Protect DB access via SSH tunnel to UDM Pro SE's PostgreSQL (port 5433, Unix socket)

## Core Components

### 1. Camera Manager (`camera_manager.py`)
- Connects to all configured Reolink cameras using `reolink_aio`
- Maintains persistent Baichuan TCP connections with auto-reconnect
- Registers event callbacks per camera
- Handles camera going offline/online gracefully

### 2. Event Classifier (`classifier.py`)
- Receives raw Baichuan push events
- Maps Reolink AI detection types to Protect smartDetect types:
  - `people` → `person`
  - `vehicle` → `vehicle`
  - `dog_cat` → `animal` (if Protect supports, otherwise skip)
- Tracks detection intervals (start/end) — same as the ONVIF tool does
- Debounce: merge detections within 2s of each other

### 3. Protect DB Writer (`protect_writer.py`)
- Connects to Protect's PostgreSQL (via SSH tunnel or direct)
- Reads camera UUIDs from Protect's `camera` table (maps IP/MAC → Protect camera ID)
- On detection start: creates event row + smartDetectObject row
- On detection end: updates event end timestamp
- Captures snapshot from camera (via Reolink HTTP API `/api.cgi?cmd=Snap`) and writes as UBV thumbnail
- Schema based on reverse-engineering from danielwoz/ubiquiti-protect-onvif-event-listener

### 4. Snapshot Capture (`snapshot.py`)
- Fetches JPEG snapshot from camera via HTTP API at detection start
- Encodes into UBV thumbnail format (from ubiquiti-protect-onvif-event-listener's ubv_thumbnail logic)
- Stores in Protect's thumbnail table/directory

### 5. Config (`config.yaml`)
```yaml
cameras:
  - name: Indkoersel
    ip: 192.168.1.151
    username: python
    password: "Ding1!Dong2?"
  - name: Altan
    ip: 192.168.1.XXX
    username: python
    password: "Ding1!Dong2?"
  # ... all 11

protect:
  # Option A: SSH tunnel (recommended)
  ssh_host: 192.168.1.1  # UDM Pro SE IP
  ssh_user: root
  db_host: /run/postgresql
  db_port: 5433
  db_name: unifi-protect
  db_user: postgres
  
  # Option B: Direct PG connection (requires pg_hba tweak on UDM)
  # db_host: 192.168.1.1
  # db_port: 5433

detection:
  pre_buffer_sec: 2
  post_buffer_sec: 2
  snapshot_on_detect: true
  
logging:
  level: INFO
  file: /var/log/reolink-protect-bridge.log
```

### 6. Main entry point (`bridge.py`)
- Loads config
- Establishes Protect DB connection (with SSH tunnel if configured)
- Starts camera manager for all configured cameras
- Runs asyncio event loop
- Graceful shutdown on SIGTERM/SIGINT

## Dependencies
- `reolink-aio` — Baichuan TCP protocol + camera API
- `asyncpg` or `psycopg2` — PostgreSQL client
- `asyncssh` — SSH tunnel to UDM (if not using direct PG)
- `pyyaml` — Config
- `Pillow` — Optional, thumbnail manipulation

## Repo Structure
```
reolink-protect-bridge/
├── README.md
├── LICENSE (MIT)
├── pyproject.toml
├── config.example.yaml
├── src/
│   └── reolink_protect_bridge/
│       ├── __init__.py
│       ├── bridge.py          # Main entry point
│       ├── camera_manager.py  # Reolink connections
│       ├── classifier.py      # Event classification
│       ├── protect_writer.py  # Protect DB writes
│       ├── snapshot.py        # Snapshot capture + UBV encoding
│       └── config.py          # Config loading
├── systemd/
│   └── reolink-protect-bridge.service
├── docker/
│   └── Dockerfile
└── tests/
    └── ...
```

## README Credits
- [danielwoz/ubiquiti-protect-onvif-event-listener](https://github.com/danielwoz/ubiquiti-protect-onvif-event-listener) — Protect DB schema reverse-engineering, UBV thumbnail format, smartDetectObject structure
- [starkillerOG/reolink_aio](https://github.com/starkillerOG/reolink_aio) — Reolink Baichuan protocol implementation, camera API
- [QuantumEntangledAndy/neolink](https://github.com/QuantumEntangledAndy/neolink) — Original Baichuan protocol research

## What's NOT in scope (v1)
- Web UI / dashboard
- HA integration (runs standalone)
- Recording management (Protect handles this)
- Multiple Protect instances
- NVR support (direct camera connections only)

## Deployment
1. Clone repo
2. Copy config.example.yaml → config.yaml, fill in camera + UDM credentials
3. `pip install .` or `docker compose up`
4. Cameras must be adopted into Protect via ONVIF first (for recording + camera UUID)
5. Service connects to cameras + Protect DB, events start appearing

## File Location
Repo: `github.com/glomotra/reolink-protect-bridge` (or personal GitHub)
Local dev: `/var/www/reolink-protect-bridge`
