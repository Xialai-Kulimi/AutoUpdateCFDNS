from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import dotenv


class ConfigError(Exception):
    """Raised when configuration is invalid, missing, or malformed."""


@dataclass
class Config:
    zone_id: str
    records_to_update: list[str]
    debug: bool = False
    interval: int = 30              # Polling interval in seconds, minimum 5
    reconcile_interval: int = 3600  # Force reconcile interval in seconds; 0 = disabled
    default_ttl: int = 1            # 1 = Cloudflare auto
    default_proxied: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def normalize_record_name(name: str) -> str:
    """Normalize domain name: lowercase, strip whitespace and trailing dot."""
    return name.strip().lower().rstrip(".")


def load_config(path: Path | str) -> Config:
    """Load and validate configuration from JSON file.

    Supports backwards compatibility with v1 config, normalizes domain names,
    deduplicates while preserving order, and preserves unknown fields in `extra`.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config file {path}: {e}") from e
    except OSError as e:
        raise ConfigError(f"Failed to read config file {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a JSON object in {path}")

    # Validate zone_id
    zone_id = raw.get("zone_id")
    if not isinstance(zone_id, str) or not zone_id.strip():
        raise ConfigError(f"Invalid or missing 'zone_id' in {path}")
    zone_id = zone_id.strip()

    # Validate records_to_update
    records_raw = raw.get("records_to_update")
    if not isinstance(records_raw, list):
        raise ConfigError(f"'records_to_update' must be a list in {path}")

    records_to_update: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(records_raw):
        if not isinstance(item, str):
            raise ConfigError(f"Record at index {idx} in 'records_to_update' must be a string")
        norm = normalize_record_name(item)
        if not norm:
            continue
        if norm not in seen:
            seen.add(norm)
            records_to_update.append(norm)

    # Optional fields with defaults
    debug = bool(raw.get("debug", False))

    interval = raw.get("interval", 30)
    if not isinstance(interval, int) or interval < 5:
        raise ConfigError(f"'interval' must be an integer >= 5 (got {interval}) in {path}")

    reconcile_interval = raw.get("reconcile_interval", 3600)
    if not isinstance(reconcile_interval, int) or reconcile_interval < 0:
        raise ConfigError(f"'reconcile_interval' must be an integer >= 0 (got {reconcile_interval}) in {path}")

    default_ttl = raw.get("default_ttl", 1)
    if not isinstance(default_ttl, int) or default_ttl < 1:
        raise ConfigError(f"'default_ttl' must be an integer >= 1 (got {default_ttl}) in {path}")

    default_proxied = bool(raw.get("default_proxied", False))

    # Known fields
    known_keys = {
        "zone_id",
        "records_to_update",
        "debug",
        "interval",
        "reconcile_interval",
        "default_ttl",
        "default_proxied",
    }
    extra = {k: v for k, v in raw.items() if k not in known_keys}

    return Config(
        zone_id=zone_id,
        records_to_update=records_to_update,
        debug=debug,
        interval=interval,
        reconcile_interval=reconcile_interval,
        default_ttl=default_ttl,
        default_proxied=default_proxied,
        extra=extra,
    )


def save_config(path: Path | str, cfg: Config) -> None:
    """Atomically save configuration to JSON file, preserving file permissions."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "zone_id": cfg.zone_id,
        "records_to_update": cfg.records_to_update,
        "debug": cfg.debug,
        "interval": cfg.interval,
        "reconcile_interval": cfg.reconcile_interval,
        "default_ttl": cfg.default_ttl,
        "default_proxied": cfg.default_proxied,
    }
    # Preserve extra fields
    data.update(cfg.extra)

    # Preserve file permissions if file exists
    mode = None
    if path.exists():
        try:
            mode = path.stat().st_mode
        except OSError:
            pass

    content = json.dumps(data, indent=4, ensure_ascii=False) + "\n"

    # Write to a temporary file in the same directory for atomic replace
    temp_fd, temp_path_str = tempfile.mkstemp(dir=str(path.parent), prefix=".config_tmp_")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        temp_path = Path(temp_path_str)
        if mode is not None:
            try:
                os.chmod(temp_path, mode)
            except OSError:
                pass

        temp_path.replace(path)
    except Exception:
        if os.path.exists(temp_path_str):
            try:
                os.unlink(temp_path_str)
            except OSError:
                pass
        raise


def config_fingerprint(path: Path | str) -> str:
    """Return SHA-256 hex digest of config file contents."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"Config file not found for fingerprint: {path}")
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        raise ConfigError(f"Failed to read config for fingerprint {path}: {e}") from e


def load_env(env_path: Path | str) -> None:
    """Load environment variables from .env if it exists."""
    path = Path(env_path).resolve()
    if path.is_file():
        dotenv.load_dotenv(path, override=False)
