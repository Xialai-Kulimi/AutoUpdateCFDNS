import os
from pathlib import Path


def project_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parent.parent


def config_path(override: Path | str | None = None) -> Path:
    """Resolve path to config.json.

    Priority:
    1. override argument
    2. CFDNS_CONFIG environment variable
    3. project_root() / "config.json"
    """
    if override:
        return Path(override).resolve()
    env_cfg = os.environ.get("CFDNS_CONFIG")
    if env_cfg:
        return Path(env_cfg).resolve()
    return project_root() / "config.json"


def env_path(cfg_path: Path | str) -> Path:
    """Resolve path to .env file (always adjacent to config file)."""
    return Path(cfg_path).resolve().parent / ".env"


def socket_path(override: Path | str | None = None) -> Path:
    """Resolve path to Unix domain socket.

    Priority:
    1. override argument
    2. CFDNS_SOCKET environment variable
    3. /run/cfdns/cfdns.sock
    """
    if override:
        return Path(override).resolve()
    env_sock = os.environ.get("CFDNS_SOCKET")
    if env_sock:
        return Path(env_sock).resolve()
    return Path("/run/cfdns/cfdns.sock")
