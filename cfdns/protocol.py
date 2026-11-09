from __future__ import annotations

import json
from pathlib import Path
import socket
from typing import Any

from cfdns.paths import socket_path as default_socket_path

MAX_PAYLOAD_SIZE = 65536  # 64 KB


class DaemonNotRunning(Exception):
    """Raised when the cfdns daemon is not running or unreachable."""


class ProtocolError(Exception):
    """Raised on socket protocol communication errors."""


def send_request(
    cmd: str,
    args: dict[str, Any] | None = None,
    socket_path: Path | str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Send a command to the cfdns daemon over Unix domain socket.

    Returns the response dict: {"ok": bool, "data": ...} or {"ok": bool, "error": ...}
    Raises DaemonNotRunning if socket is missing or refused.
    Raises PermissionError if permissions prevent connecting.
    """
    sock_path = default_socket_path(socket_path)

    if not sock_path.exists():
        raise DaemonNotRunning(f"cfdns daemon is not running (socket {sock_path} not found)")

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)

    try:
        s.connect(str(sock_path))
    except (ConnectionRefusedError, FileNotFoundError) as e:
        raise DaemonNotRunning(f"cfdns daemon is not running (connection failed to {sock_path})") from e
    except PermissionError as e:
        raise PermissionError(
            f"Permission denied accessing cfdns socket at {sock_path}. "
            "Please run with appropriate permissions or as user 'kulimi'."
        ) from e

    try:
        payload = json.dumps({"cmd": cmd, "args": args or {}}, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(payload) > MAX_PAYLOAD_SIZE:
            raise ProtocolError(f"Request payload exceeds max limit of {MAX_PAYLOAD_SIZE} bytes")

        s.sendall(payload)

        # Read response line
        response_bytes = bytearray()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response_bytes.extend(chunk)
            if len(response_bytes) > MAX_PAYLOAD_SIZE:
                raise ProtocolError(f"Response exceeded max payload limit of {MAX_PAYLOAD_SIZE} bytes")
            if b"\n" in response_bytes:
                break

        line = response_bytes.split(b"\n", 1)[0].decode("utf-8")
        if not line:
            raise ProtocolError("Empty response received from daemon")

        try:
            resp = json.loads(line)
        except json.JSONDecodeError as e:
            raise ProtocolError(f"Invalid JSON response from daemon: {e}") from e

        if not isinstance(resp, dict) or "ok" not in resp:
            raise ProtocolError(f"Malformed response envelope from daemon: {resp}")

        return resp
    finally:
        try:
            s.close()
        except OSError:
            pass
