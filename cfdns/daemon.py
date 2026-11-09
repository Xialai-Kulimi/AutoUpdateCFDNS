from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import signal
import socket
import socketserver
import sys
import threading
from typing import Any, Callable

from cfdns.cf_client import CloudflareClient
from cfdns.config import load_env
from cfdns.ipsource import get_public_ipv4, validate_ipv4
from cfdns.paths import config_path as default_config_path, env_path, socket_path as default_socket_path
from cfdns.protocol import MAX_PAYLOAD_SIZE
from cfdns.service import DDNSService

logger = logging.getLogger("cfdns.daemon")


class ThreadedUnixStreamServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class UnixSocketHandler(socketserver.StreamRequestHandler):
    server: ThreadedUnixStreamServer  # for type hinting

    def handle(self) -> None:
        service: DDNSService = self.server.service  # type: ignore

        try:
            line = self.rfile.readline(MAX_PAYLOAD_SIZE + 1)
            if not line:
                return

            if len(line) > MAX_PAYLOAD_SIZE:
                resp = {"ok": False, "error": f"Request exceeded max size of {MAX_PAYLOAD_SIZE} bytes"}
                self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
                self.wfile.flush()
                return

            try:
                req = json.loads(line.decode("utf-8"))
            except Exception as e:
                resp = {"ok": False, "error": f"Invalid JSON request: {e}"}
                self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
                self.wfile.flush()
                return

            if not isinstance(req, dict):
                resp = {"ok": False, "error": "Request must be a JSON object"}
                self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
                self.wfile.flush()
                return

            cmd = req.get("cmd")
            args = req.get("args") or {}

            # Dispatch command
            try:
                if cmd == "ping":
                    result = "pong"
                elif cmd == "status":
                    result = service.status()
                elif cmd == "config":
                    result = service.get_config()
                elif cmd == "list":
                    result = service.list_records()
                elif cmd == "sync":
                    req_ip = args.get("ip")
                    if req_ip:
                        req_ip = validate_ipv4(req_ip)
                    sync_res = service.sync(ip=req_ip, force=args.get("force", False))
                    result = asdict(sync_res)
                elif cmd == "add":
                    name = args.get("name")
                    if not name:
                        raise ValueError("Missing 'name' argument for add")
                    add_res = service.add_domain(
                        name=name,
                        proxied=args.get("proxied"),
                        ttl=args.get("ttl"),
                        force=args.get("force", False),
                    )
                    result = asdict(add_res)
                elif cmd == "remove":
                    name = args.get("name")
                    if not name:
                        raise ValueError("Missing 'name' argument for remove")
                    rem_res = service.remove_domain(
                        name=name,
                        delete_record=args.get("delete_record", False),
                    )
                    result = asdict(rem_res)
                else:
                    raise ValueError(f"Unknown command: '{cmd}'")

                resp = {"ok": True, "data": result}
            except Exception as e:
                resp = {"ok": False, "error": str(e)}

            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
            self.wfile.flush()
        except Exception as e:
            logger.error("Error handling socket connection: %s", e)


def setup_logging() -> None:
    """Configure stdout logging for systemd / journald."""
    log_level_name = os.environ.get("CFDNS_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Remove existing handlers to avoid duplicates
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def run_daemon(
    config_path: Path | str | None = None,
    socket_path: Path | str | None = None,
    cf_factory: Callable[[Any], CloudflareClient] | None = None,
    ip_getter: Callable[[], str] | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Run the cfdns background daemon."""
    setup_logging()

    cfg_path = default_config_path(config_path)
    sock_path = default_socket_path(socket_path)

    logger.info("Starting cfdns daemon (config: %s, socket: %s)", cfg_path, sock_path)

    # Load environment variables
    load_env(env_path(cfg_path))

    # Setup IP getter fallback
    if ip_getter is None:
        def default_ip_getter() -> str:
            cmd = os.environ.get("IP_COMMAND")
            return get_public_ipv4(cmd)

        ip_getter = default_ip_getter

    # Setup CF client factory fallback
    if cf_factory is None:
        def default_cf_factory(cfg: Any) -> CloudflareClient:
            return CloudflareClient(
                api_email=os.environ.get("API_EMAIL"),
                api_key=os.environ.get("API_KEY"),
                zone_id=cfg.zone_id,
            )

        cf_factory = default_cf_factory

    # Create service
    service = DDNSService(
        config_path=cfg_path,
        cf_factory=cf_factory,
        ip_getter=ip_getter,
    )

    # Check existing socket
    if sock_path.exists():
        test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            test_sock.connect(str(sock_path))
            test_sock.close()
            logger.error("Another daemon instance is already active on socket %s", sock_path)
            sys.exit(1)
        except (ConnectionRefusedError, FileNotFoundError):
            logger.info("Removing stale socket at %s", sock_path)
            try:
                sock_path.unlink()
            except OSError as e:
                logger.error("Failed to remove stale socket: %s", e)
                sys.exit(1)

    sock_path.parent.mkdir(parents=True, exist_ok=True)

    server = ThreadedUnixStreamServer(str(sock_path), UnixSocketHandler)
    server.service = service  # type: ignore

    # Set socket permissions (0660: rw-rw----)
    try:
        os.chmod(str(sock_path), 0o660)
    except OSError as e:
        logger.warning("Could not set socket permissions: %s", e)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True, name="cfdns-socket-server")
    server_thread.start()

    if stop_event is None:
        stop_event = threading.Event()

    def handle_signal(sig: int, frame: Any) -> None:
        sig_name = signal.Signals(sig).name
        logger.info("Received %s signal; shutting down...", sig_name)
        stop_event.set()

    # Install signal handlers only in main thread
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

    # Perform initial tick
    try:
        service.tick()
    except Exception as e:
        logger.exception("Initial tick encountered an error: %s", e)

    # Main periodic loop
    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=float(service.config.interval))
            if stop_event.is_set():
                break

            try:
                service.tick()
            except Exception as e:
                logger.exception("Unexpected error in daemon tick: %s", e)
    finally:
        logger.info("Shutting down socket server...")
        server.shutdown()
        server.server_close()
        if sock_path.exists():
            try:
                sock_path.unlink()
            except OSError:
                pass
        logger.info("cfdns daemon stopped successfully.")
