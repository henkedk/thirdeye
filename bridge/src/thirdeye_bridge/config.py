"""YAML config loading with environment variable expansion."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: str) -> str:
    """Replace ${VAR_NAME} placeholders with environment variable values."""

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        val = os.environ.get(name)
        if val is None:
            raise ValueError(f"environment variable {name} is not set")
        return val

    return _ENV_VAR_RE.sub(_replace, value)


def _expand_recursive(obj):
    """Recursively expand env vars in strings throughout a data structure."""
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_recursive(v) for v in obj]
    return obj


@dataclass
class InjectorConfig:
    url: str = "http://192.168.1.1:9090"
    token: str = ""


@dataclass
class CameraCredentials:
    username: str = "admin"
    password: str = ""


@dataclass
class DiscoveryConfig:
    poll_interval_sec: int = 60


@dataclass
class DetectionConfig:
    debounce_sec: float = 2.0
    snapshot_on_detect: bool = True
    default_score: int = 85


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str | None = None


@dataclass
class BridgeConfig:
    injector: InjectorConfig = field(default_factory=InjectorConfig)
    camera_defaults: CameraCredentials = field(default_factory=CameraCredentials)
    camera_overrides: dict[str, CameraCredentials] = field(default_factory=dict)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load_config(path: str | Path) -> BridgeConfig:
    """Load and validate config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    raw = _expand_recursive(raw)

    cfg = BridgeConfig()

    if "injector" in raw:
        inj = raw["injector"]
        cfg.injector = InjectorConfig(
            url=inj.get("url", cfg.injector.url),
            token=inj.get("token", cfg.injector.token),
        )

    if "camera_defaults" in raw:
        cd = raw["camera_defaults"]
        cfg.camera_defaults = CameraCredentials(
            username=cd.get("username", cfg.camera_defaults.username),
            password=cd.get("password", cfg.camera_defaults.password),
        )

    if "camera_overrides" in raw:
        for ip, creds in raw["camera_overrides"].items():
            cfg.camera_overrides[ip] = CameraCredentials(
                username=creds.get("username", cfg.camera_defaults.username),
                password=creds.get("password", cfg.camera_defaults.password),
            )

    if "discovery" in raw:
        d = raw["discovery"]
        cfg.discovery = DiscoveryConfig(
            poll_interval_sec=d.get("poll_interval_sec", cfg.discovery.poll_interval_sec),
        )

    if "detection" in raw:
        det = raw["detection"]
        cfg.detection = DetectionConfig(
            debounce_sec=det.get("debounce_sec", cfg.detection.debounce_sec),
            snapshot_on_detect=det.get("snapshot_on_detect", cfg.detection.snapshot_on_detect),
            default_score=det.get("default_score", cfg.detection.default_score),
        )

    if "logging" in raw:
        log = raw["logging"]
        cfg.logging = LoggingConfig(
            level=log.get("level", cfg.logging.level),
            file=log.get("file", cfg.logging.file),
        )

    if not cfg.injector.token:
        raise ValueError("injector.token is required")
    if not cfg.camera_defaults.password:
        raise ValueError("camera_defaults.password is required")

    return cfg
