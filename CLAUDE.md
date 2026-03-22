# CLAUDE.md — reolink-protect-bridge

## What
Python service that bridges Reolink camera AI smart detections (person/vehicle/animal) into UniFi Protect's native timeline UI via the Baichuan TCP protocol and direct PostgreSQL writes.

## Architecture
- `reolink_aio` for Baichuan TCP push events from cameras
- `asyncpg` + SSH tunnel for Protect's PostgreSQL on UDM Pro SE
- Protect DB schema from danielwoz/ubiquiti-protect-onvif-event-listener
- Runs on any LAN host (Proxmox, Docker, etc.) — NOT on the UDM itself

## Project Structure
```
src/reolink_protect_bridge/
├── bridge.py          # Main entry point + asyncio loop
├── camera_manager.py  # Reolink Baichuan connections + reconnect
├── classifier.py      # Map Reolink AI types → Protect smartDetect types
├── protect_writer.py  # PostgreSQL writes to Protect DB
├── snapshot.py        # Snapshot capture + UBV thumbnail encoding
└── config.py          # YAML config loading
```

## Key Decisions
- Baichuan TCP (not ONVIF) — Reolink doesn't expose AI detections via ONVIF
- Dual Baichuan connections confirmed working (HA + bridge coexist)
- SSH tunnel to UDM PostgreSQL (not direct PG exposure)
- Camera admin creds: user `python`, standard password

## Dev Commands
```bash
pip install -e .                    # Install in dev mode
python -m reolink_protect_bridge    # Run the bridge
```

## Dependencies
- reolink-aio (Baichuan protocol)
- asyncpg (PostgreSQL async)
- asyncssh (SSH tunnel to UDM)
- pyyaml (config)

## Credits
- danielwoz/ubiquiti-protect-onvif-event-listener — Protect DB schema + UBV format
- starkillerOG/reolink_aio — Baichuan protocol
- QuantumEntangledAndy/neolink — Original Baichuan research
