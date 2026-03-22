# 👁️ thirdeye

**Smart detection bridge for UniFi Protect.**

Inject person, vehicle, and animal AI detections from third-party cameras into UniFi Protect's native timeline — as if they were built-in.

---

UniFi Protect lets you adopt third-party cameras via ONVIF. But those cameras lose their AI smarts in the process — no person detection, no vehicle alerts, no smart filtering. Just basic motion.

thirdeye fixes that. It listens to your cameras' native AI detection feeds and injects the events directly into Protect's database. Your third-party cameras get the same smart detection UI as native UniFi cameras.

## How it works

```
┌──────────────┐                     ┌─────────────────────┐
│  Your        │    Native AI        │  thirdeye-bridge    │
│  Cameras     │◄───────────────────►│  (Python)           │
│              │    protocol         │  runs on your LAN   │
└──────────────┘                     └──────────┬──────────┘
                                                │
                                          HTTP (encrypted)
                                          IP-locked + token
                                                │
                                     ┌──────────▼──────────┐
                                     │  thirdeye-injector  │
                                     │  (Go binary)        │
                                     │  runs on your UDM   │
                                     └──────────┬──────────┘
                                                │
                                          Local DB socket
                                                │
                                     ┌──────────▼──────────┐
                                     │  UniFi Protect      │
                                     └─────────────────────┘
```

**Two components, clear separation:**

| Component | Language | Runs on | Purpose |
|-----------|----------|---------|---------|
| **thirdeye-injector** | Go | UDM Pro / UDM SE | Validated database writes. 4 endpoints, ~800 lines. |
| **thirdeye-bridge** | Python | Any LAN host | Camera connections, AI event processing, snapshots. |

The injector is intentionally minimal — a locked-down gate to Protect's database. All the complexity (camera protocols, event classification, thumbnail capture) stays in the bridge, running safely on your own hardware.

## Supported cameras

| Vendor | Protocol | Status |
|--------|----------|--------|
| **Reolink** | Baichuan TCP | ✅ Supported |
| Hikvision | ISAPI | 🔜 Planned |
| Dahua | proprietary | 🔜 Planned |

> thirdeye's injector is vendor-agnostic. Adding a new camera brand means writing a new bridge module — the injector doesn't change.

## Detection types

The injector supports **every smart detection type** that UniFi Protect understands. Bridge clients supply whatever their cameras or AI pipeline can deliver.

### Visual

| Type | Protect UI | Reolink bridge |
|------|-----------|----------------|
| 🧑 `person` | Smart Detection → Person | ✅ Native Baichuan |
| 🚗 `vehicle` | Smart Detection → Vehicle | ✅ Native Baichuan |
| 🐕 `animal` | Smart Detection → Animal | ✅ Native Baichuan |
| 📦 `package` | Smart Detection → Package | 🔜 Doorbell models |
| 🪪 `licensePlate` | License Plate Recognition | 🔜 Via AI middleware |
| 👤 `face` | Face Recognition | 🔜 Via AI middleware |

### Audio

| Type | Protect UI | Reolink bridge |
|------|-----------|----------------|
| 🔥 `smoke` | Smoke Alarm | 🔜 Via AI middleware |
| 💨 `cmonx` | CO Alarm | 🔜 Via AI middleware |
| 🐕 `bark` | Dog Bark | 🔜 Via AI middleware |
| 🚨 `burglar` | Burglar Alarm | 🔜 Via AI middleware |
| 💥 `glass_break` | Glass Break | 🔜 Via AI middleware |
| 🚙 `car_alarm` | Car Alarm | 🔜 Via AI middleware |
| 📢 `car_horn` | Car Horn | 🔜 Via AI middleware |
| 🗣️ `speak` | Speech | 🔜 Via AI middleware |
| 👶 `baby_cry` | Baby Cry | 🔜 Via AI middleware |

> **The injector doesn't care where detections come from.** Direct camera AI, a local Frigate/CodeProject.AI instance, a custom ML pipeline — anything that can POST JSON with a type and thumbnail works.

Events appear in Protect's timeline with thumbnails, timestamps, and proper smart detection labels. Filter, search, and get alerts — just like native cameras.

## Quick start

### 1. Install the injector on your UDM

SSH into your UDM (UniFi OS → System → Advanced → Enable SSH):

```bash
# Download the latest release
curl -L https://github.com/henkedk/thirdeye/releases/latest/download/thirdeye-injector-arm64 \
  -o /data/thirdeye-injector/thirdeye-injector
chmod +x /data/thirdeye-injector/thirdeye-injector

# Create config
cat > /data/thirdeye-injector/config.yaml << 'EOF'
listen: "0.0.0.0:9090"
allow_from:
  - "192.168.1.50"     # IP of your bridge host
token: "your-secret-token-here"
db:
  socket: "/run/postgresql"
  port: 5433
  name: "unifi-protect"
  user: "postgres"
EOF

# Install and start
curl -L https://github.com/henkedk/thirdeye/releases/latest/download/thirdeye-injector.service \
  -o /data/thirdeye-injector/thirdeye-injector.service
ln -sf /data/thirdeye-injector/thirdeye-injector.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now thirdeye-injector
```

### 2. Auto-restore after firmware updates (optional)

```bash
mkdir -p /data/on_boot.d
cat > /data/on_boot.d/10-thirdeye-injector.sh << 'EOF'
#!/bin/bash
ln -sf /data/thirdeye-injector/thirdeye-injector.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now thirdeye-injector
EOF
chmod +x /data/on_boot.d/10-thirdeye-injector.sh
```

### 3. Run the bridge

```bash
pip install thirdeye-bridge
```

Create `config.yaml`:

```yaml
injector:
  url: "http://192.168.1.1:9090"
  token: "your-secret-token-here"

camera_defaults:
  username: "thirdeye"
  password: "your-camera-password"

detection:
  debounce_sec: 2
  snapshot_on_detect: true
```

```bash
thirdeye-bridge --config config.yaml
```

Or with Docker:

```bash
docker run -v ./config.yaml:/app/config.yaml henkedk/thirdeye-bridge
```

### 4. That's it

Cameras are discovered automatically from Protect's database. New cameras adopted into Protect are picked up within 60 seconds.

> **⚠️ Camera credential setup:** All cameras need the same local user account for auto-discovery to work. Create a user (e.g. `thirdeye`) with the same password on each camera via the Reolink app/web UI before starting the bridge. Per-camera credential overrides are supported in config if needed.

## Security

thirdeye writes to your Protect database. We take that seriously.

| Layer | What it does |
|-------|-------------|
| **IP allowlist** | Injector only accepts connections from configured IPs |
| **Token auth** | Shared secret in every request, constant-time comparison |
| **Schema validation** | Injector verifies Protect's DB schema on startup, refuses to run if it changed |
| **Input validation** | Every field checked: UUID format, timestamp sanity, JPEG magic bytes, camera existence |
| **Rate limiting** | Per-camera event rate cap prevents runaway loops |
| **Transaction safety** | Event + detection + thumbnail in one atomic transaction |
| **Event tagging** | Every injected event tagged in metadata for easy identification and clean removal |
| **Dry run mode** | Test everything without writing to the database |

### Clean removal

Every event thirdeye creates is tagged. Full removal:

```bash
python scripts/cleanup.py --injector-url http://192.168.1.1:9090 --token your-token
```

This removes all thirdeye events, smart detect objects, and thumbnails, and reverts camera smart detect settings. As if it was never installed.

## Persistence

The injector lives in `/data/thirdeye-injector/` on your UDM. The `/data/` volume survives firmware updates. With the optional boot hook, the service auto-restores after any update — zero manual intervention.

## Prerequisites

- **UniFi Protect** running on a UDM Pro, UDM Pro SE, or UDM SE
- **SSH access** to the UDM (UniFi OS → System → Advanced)
- **Cameras adopted into Protect** via ONVIF (for recording + camera UUID assignment)
- **Same local user account** on all cameras (for auto-discovery)
- A Linux host on your LAN for the bridge (Proxmox VM, NAS, Raspberry Pi, Docker host, etc.)

## How it's built

thirdeye wouldn't exist without the work of:

- **[danielwoz/ubiquiti-protect-onvif-event-listener](https://github.com/danielwoz/ubiquiti-protect-onvif-event-listener)** (Apache 2.0) — Reverse-engineered Protect's DB schema, discovered the thumbnailId routing mechanism, and mapped the smartDetectObject structure.
- **[starkillerOG/reolink_aio](https://github.com/starkillerOG/reolink_aio)** (MIT) — Clean Python implementation of Reolink's Baichuan TCP protocol.
- **[QuantumEntangledAndy/neolink](https://github.com/QuantumEntangledAndy/neolink)** (AGPL-3.0) — Original Baichuan protocol research. No code used.

### Legal

This project writes to UniFi Protect's database for interoperability purposes. Reverse-engineering for interoperability is protected under [EU Directive 2009/24/EC](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A32009L0024) (Article 6) and [US DMCA §1201(f)](https://www.law.cornell.edu/uscode/text/17/1201).

thirdeye is not affiliated with or endorsed by Ubiquiti Inc. or Reolink.

## License

[MIT](LICENSE) — Copyright (c) 2026 Jens Henke
