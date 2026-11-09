from __future__ import annotations

import json
from pathlib import Path
import threading
import time
import pytest

from cfdns.cf_client import DNSRecord
from cfdns.daemon import run_daemon
from cfdns.protocol import MAX_PAYLOAD_SIZE, DaemonNotRunning, send_request
from tests.fakes import FakeCloudflareClient


@pytest.fixture
def running_daemon(tmp_path: Path):
    """Start a real daemon instance in a background thread."""
    cfg_file = tmp_path / "config.json"
    sock_file = tmp_path / "cfdns.sock"

    cfg_data = {
        "zone_id": "socket-test-zone",
        "records_to_update": ["kulimi.tw", "app.kulimi.tw"],
        "debug": False,
        "interval": 30,
        "reconcile_interval": 3600,
        "default_ttl": 1,
        "default_proxied": False,
    }
    cfg_file.write_text(json.dumps(cfg_data, indent=4))

    records = [
        DNSRecord(id="r1", name="kulimi.tw", type="A", content="1.1.1.1", proxied=False, ttl=1),
        DNSRecord(id="r2", name="app.kulimi.tw", type="A", content="2.2.2.2", proxied=False, ttl=1),
    ]
    fake_cf = FakeCloudflareClient(zone_id="socket-test-zone", zone_name="kulimi.tw", records=records)

    stop_event = threading.Event()
    current_ip_box = ["1.1.1.1"]

    daemon_thread = threading.Thread(
        target=run_daemon,
        kwargs={
            "config_path": cfg_file,
            "socket_path": sock_file,
            "cf_factory": lambda cfg: fake_cf,
            "ip_getter": lambda: current_ip_box[0],
            "stop_event": stop_event,
        },
        daemon=True,
    )
    daemon_thread.start()

    # Wait for socket to become available
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if sock_file.exists():
            try:
                pong = send_request("ping", socket_path=sock_file, timeout=1.0)
                if pong.get("ok"):
                    break
            except Exception:
                pass
        time.sleep(0.05)
    else:
        stop_event.set()
        daemon_thread.join(timeout=2.0)
        pytest.fail("Daemon did not start socket in time")

    yield {
        "config_file": cfg_file,
        "socket_file": sock_file,
        "fake_cf": fake_cf,
        "stop_event": stop_event,
        "thread": daemon_thread,
        "current_ip_box": current_ip_box,
    }

    # Teardown
    stop_event.set()
    daemon_thread.join(timeout=3.0)
    assert not sock_file.exists(), "Socket file should be removed upon daemon stop"


def test_socket_ping(running_daemon: dict) -> None:
    sock = running_daemon["socket_file"]
    resp = send_request("ping", socket_path=sock)
    assert resp == {"ok": True, "data": "pong"}


def test_socket_status(running_daemon: dict) -> None:
    sock = running_daemon["socket_file"]
    resp = send_request("status", socket_path=sock)
    assert resp["ok"] is True
    data = resp["data"]
    assert data["version"] == "2.0.0"
    assert data["zone_id"] == "socket-test-zone"
    assert data["zone_name"] == "kulimi.tw"
    assert data["current_ip"] == "1.1.1.1"
    assert data["tracked_count"] == 2


def test_socket_config(running_daemon: dict) -> None:
    sock = running_daemon["socket_file"]
    resp = send_request("config", socket_path=sock)
    assert resp["ok"] is True
    data = resp["data"]
    assert data["zone_id"] == "socket-test-zone"
    assert "records_to_update" in data
    # Ensure env info does NOT leak API_KEY value
    assert isinstance(data["env"]["API_KEY"], bool)
    assert isinstance(data["env"]["API_EMAIL"], bool)


def test_socket_list(running_daemon: dict) -> None:
    sock = running_daemon["socket_file"]
    fake_cf: FakeCloudflareClient = running_daemon["fake_cf"]
    # Change app.kulimi.tw to a different IP so it shows up as stale
    fake_cf.records["r2"].content = "9.9.9.9"

    resp = send_request("list", socket_path=sock)
    assert resp["ok"] is True
    items = resp["data"]
    assert len(items) == 2
    by_name = {i["name"]: i for i in items}
    assert by_name["kulimi.tw"]["state"] == "ok"
    assert by_name["app.kulimi.tw"]["state"] == "stale"


def test_socket_sync(running_daemon: dict) -> None:
    sock = running_daemon["socket_file"]
    fake_cf: FakeCloudflareClient = running_daemon["fake_cf"]

    # Sync with new IP
    resp = send_request("sync", args={"ip": "8.8.8.8"}, socket_path=sock)
    assert resp["ok"] is True
    data = resp["data"]
    assert data["ip"] == "8.8.8.8"
    assert sorted(data["updated"]) == ["app.kulimi.tw", "kulimi.tw"]

    # Verify CF records were updated
    assert fake_cf.records["r1"].content == "8.8.8.8"
    assert fake_cf.records["r2"].content == "8.8.8.8"


def test_socket_add_and_remove(running_daemon: dict) -> None:
    sock = running_daemon["socket_file"]
    fake_cf: FakeCloudflareClient = running_daemon["fake_cf"]

    # Add domain
    add_resp = send_request(
        "add",
        args={"name": "newservice", "proxied": True, "ttl": 120},
        socket_path=sock,
    )
    assert add_resp["ok"] is True
    data = add_resp["data"]
    assert data["name"] == "newservice.kulimi.tw"
    assert data["action"] == "created"
    assert data["proxied"] is True

    # Verify created in fake CF
    rec = [r for r in fake_cf.records.values() if r.name == "newservice.kulimi.tw"][0]
    assert rec.proxied is True
    assert rec.ttl == 120

    # Remove domain with delete_record=True
    rem_resp = send_request(
        "remove",
        args={"name": "newservice.kulimi.tw", "delete_record": True},
        socket_path=sock,
    )
    assert rem_resp["ok"] is True
    rem_data = rem_resp["data"]
    assert rem_data["removed_from_config"] is True
    assert rem_data["record_deleted"] is True
    assert not any(r.name == "newservice.kulimi.tw" for r in fake_cf.records.values())


def test_socket_error_handling(running_daemon: dict) -> None:
    sock = running_daemon["socket_file"]

    # Unknown command
    resp = send_request("unknown_action", socket_path=sock)
    assert resp["ok"] is False
    assert "Unknown command" in resp["error"]

    # Daemon survives and answers subsequent requests
    pong = send_request("ping", socket_path=sock)
    assert pong["ok"] is True


def test_daemon_not_running(tmp_path: Path) -> None:
    nonexistent = tmp_path / "not_there.sock"
    with pytest.raises(DaemonNotRunning):
        send_request("ping", socket_path=nonexistent)
