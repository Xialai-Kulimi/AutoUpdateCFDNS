"""Regression tests for issues found during code review."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cfdns.service import DDNSService
from tests.fakes import FakeCloudflareClient


def _service(cfg: Path, cf: FakeCloudflareClient, ip_getter=lambda: "1.2.3.4") -> DDNSService:
    return DDNSService(config_path=cfg, cf_factory=lambda c: cf, ip_getter=ip_getter, clock=lambda: 1000.0)


def test_tick_does_not_raise_when_cloudflare_list_fails(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    fake_cf.fail_on_list = True
    service = _service(tmp_config_file, fake_cf)
    service.tick()  # must not raise
    assert service.last_synced_ip is None
    assert service.last_error and "Cloudflare" in service.last_error

    # Recovers on the next tick once Cloudflare is reachable again
    fake_cf.fail_on_list = False
    service.tick()
    assert service.last_synced_ip == "1.2.3.4"
    assert service.last_error is None


def test_tick_clears_stale_ip_error_after_recovery(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    state = {"fail": False}

    def ip_getter() -> str:
        if state["fail"]:
            raise RuntimeError("ppp0 down")
        return "1.2.3.4"

    service = _service(tmp_config_file, fake_cf, ip_getter)
    service.tick()
    state["fail"] = True
    service.tick()
    assert service.last_error and service.last_error.startswith("Failed to get public IP")
    state["fail"] = False
    service.tick()  # same IP, no sync needed, but error must be cleared
    assert service.last_error is None


@pytest.mark.parametrize("ttl", [0, 30, 59, 86401])
def test_add_domain_rejects_invalid_ttl(tmp_config_file: Path, fake_cf: FakeCloudflareClient, ttl: int) -> None:
    service = _service(tmp_config_file, fake_cf)
    with pytest.raises(ValueError, match="Invalid TTL"):
        service.add_domain("newapp", ttl=ttl)
    assert not any(c["method"] == "create_record" for c in fake_cf.calls)


def test_remove_keeps_tracking_when_cloudflare_delete_fails(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    fake_cf.fail_on_delete_ids.add("rec-2")
    service = _service(tmp_config_file, fake_cf)
    service.current_ip = "1.2.3.4"
    with pytest.raises(Exception):
        service.remove_domain("app", delete_record=True)
    cfg = json.loads(tmp_config_file.read_text())
    assert "app.kulimi.tw" in cfg["records_to_update"]
    assert "app.kulimi.tw" in service.config.records_to_update
