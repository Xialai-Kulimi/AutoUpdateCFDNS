from __future__ import annotations

import json
from pathlib import Path
import pytest

from cfdns.cf_client import DNSRecord
from cfdns.config import save_config
from cfdns.service import DDNSService, is_valid_hostname
from tests.fakes import FakeCloudflareClient


def test_is_valid_hostname() -> None:
    assert is_valid_hostname("kulimi.tw") is True
    assert is_valid_hostname("app.kulimi.tw") is True
    assert is_valid_hostname("*.kulimi.tw") is True
    assert is_valid_hostname("sub.app.kulimi.tw") is True

    # Invalid
    assert is_valid_hostname("") is False
    assert is_valid_hostname("-app.kulimi.tw") is False
    assert is_valid_hostname("app-.kulimi.tw") is False
    assert is_valid_hostname("app..kulimi.tw") is False
    assert is_valid_hostname("app_test.kulimi.tw") is False  # Underscore invalid in hostname
    assert is_valid_hostname("app." + "a" * 64 + ".kulimi.tw") is False  # Label > 63 chars


def test_service_sync_stale_ok_missing(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    # Initial state in fake_cf:
    # kulimi.tw -> 1.2.3.4
    # app.kulimi.tw -> 1.2.3.4
    # api.kulimi.tw -> 5.6.7.8
    # config records_to_update: ["kulimi.tw", "app.kulimi.tw", "api.kulimi.tw", "missing.kulimi.tw"]
    cfg = json.loads(tmp_config_file.read_text())
    cfg["records_to_update"].append("missing.kulimi.tw")
    tmp_config_file.write_text(json.dumps(cfg))

    current_time = 1000.0
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
        clock=lambda: current_time,
    )

    result = service.sync(ip="1.2.3.4")
    assert result.ip == "1.2.3.4"
    # kulimi.tw and app.kulimi.tw match 1.2.3.4 -> ok
    assert sorted(result.ok) == ["app.kulimi.tw", "kulimi.tw"]
    # api.kulimi.tw had 5.6.7.8 -> updated
    assert result.updated == ["api.kulimi.tw"]
    # missing.kulimi.tw not in CF -> missing
    assert result.missing == ["missing.kulimi.tw"]
    assert result.errors == {}
    assert service.last_synced_ip == "1.2.3.4"
    assert fake_cf.records["rec-3"].content == "1.2.3.4"


def test_service_sync_partial_failure_does_not_set_last_synced_ip(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    current_time = 1000.0
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "9.9.9.9",
        clock=lambda: current_time,
    )

    # Inject failure on api.kulimi.tw
    fake_cf.fail_on_update_names.add("api.kulimi.tw")

    result = service.sync(ip="9.9.9.9")
    assert "api.kulimi.tw" in result.errors
    assert "kulimi.tw" in result.updated
    assert "app.kulimi.tw" in result.updated
    # Because there were errors, last_synced_ip must remain None
    assert service.last_synced_ip is None

    # Next tick must retry because last_synced_ip != current_ip
    fake_cf.fail_on_update_names.clear()
    fake_cf.calls.clear()

    service.tick()
    # It should have called list_records and updated api.kulimi.tw
    assert service.last_synced_ip == "9.9.9.9"
    assert fake_cf.records["rec-3"].content == "9.9.9.9"


def test_service_sync_debug_mode_does_not_write_cf(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    # Set debug: true in config
    cfg = json.loads(tmp_config_file.read_text())
    cfg["debug"] = True
    tmp_config_file.write_text(json.dumps(cfg))

    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "9.9.9.9",
    )

    fake_cf.calls.clear()
    result = service.sync(ip="9.9.9.9")

    assert result.dry_run is True
    # In fake_cf calls, there should be NO update_record
    update_calls = [c for c in fake_cf.calls if c["method"] == "update_record"]
    assert len(update_calls) == 0
    # Values in fake_cf remain unchanged
    assert fake_cf.records["rec-1"].content == "1.2.3.4"


def test_service_tick_ip_unchanged_skips_list(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    now = 1000.0
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
        clock=lambda: now,
    )

    # First tick syncs
    service.tick()
    assert service.last_synced_ip == "1.2.3.4"

    fake_cf.calls.clear()
    # Second tick with same IP and reconcile interval not reached
    service.tick()
    # fake_cf should NOT have any calls to list_records
    list_calls = [c for c in fake_cf.calls if c["method"] == "list_records"]
    assert len(list_calls) == 0


def test_service_tick_reconciles_after_interval(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    now = 1000.0
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
        clock=lambda: now,
    )
    service.tick()

    fake_cf.calls.clear()
    # Advance time beyond reconcile_interval (3600s)
    now += 3601.0
    service.tick()

    # list_records should have been called for reconciliation
    list_calls = [c for c in fake_cf.calls if c["method"] == "list_records"]
    assert len(list_calls) > 0


def test_service_add_domain_creation(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
    )

    # Short name "blog" -> "blog.kulimi.tw"
    res = service.add_domain("blog", proxied=True, ttl=300)
    assert res.name == "blog.kulimi.tw"
    assert res.action == "created"
    assert res.proxied is True
    assert res.ttl == 300
    assert res.already_tracked is False

    # Check CF fake has new record
    created = [r for r in fake_cf.records.values() if r.name == "blog.kulimi.tw"]
    assert len(created) == 1
    assert created[0].content == "1.2.3.4"
    assert created[0].proxied is True
    assert created[0].ttl == 300

    # Check config was updated
    cfg = json.loads(tmp_config_file.read_text())
    assert "blog.kulimi.tw" in cfg["records_to_update"]


def test_service_add_domain_apex_at(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
    )

    res = service.add_domain("@")
    assert res.name == "kulimi.tw"
    # kulimi.tw already points to 1.2.3.4
    assert res.action == "exists"
    assert res.already_tracked is True  # Already in test config


def test_service_add_domain_validation_and_zone_check(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
    )

    # Does not belong to zone
    with pytest.raises(ValueError, match="does not belong to zone"):
        service.add_domain("foo.otherdomain.com")

    # Invalid hostname
    with pytest.raises(ValueError, match="Invalid domain name"):
        service.add_domain("-invalid.kulimi.tw")


def test_service_add_domain_cname_conflict(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    # Add a CNAME record
    fake_cf.records["rec-cname"] = DNSRecord(
        id="rec-cname",
        name="alias.kulimi.tw",
        type="CNAME",
        content="target.kulimi.tw",
        proxied=False,
        ttl=1,
    )

    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
    )

    with pytest.raises(ValueError, match="CNAME record already exists"):
        service.add_domain("alias.kulimi.tw")


def test_service_add_domain_force_and_no_force(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    # api.kulimi.tw points to 5.6.7.8; local IP is 1.2.3.4
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
    )

    # Without force -> error
    with pytest.raises(ValueError, match="currently points to 5.6.7.8.*--force"):
        service.add_domain("api.kulimi.tw", force=False)

    # With force -> updated
    res = service.add_domain("api.kulimi.tw", force=True)
    assert res.action == "updated"
    assert fake_cf.records["rec-3"].content == "1.2.3.4"


def test_service_add_domain_debug_mode_writes_config_not_cf(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    cfg = json.loads(tmp_config_file.read_text())
    cfg["debug"] = True
    tmp_config_file.write_text(json.dumps(cfg))

    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
    )

    fake_cf.calls.clear()
    res = service.add_domain("newsub")
    assert res.dry_run is True
    assert res.action == "created"

    # CF API create was NOT called
    creates = [c for c in fake_cf.calls if c["method"] == "create_record"]
    assert len(creates) == 0

    # Config file WAS updated
    reloaded_cfg = json.loads(tmp_config_file.read_text())
    assert "newsub.kulimi.tw" in reloaded_cfg["records_to_update"]


def test_service_remove_domain(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
    )

    # Remove non-tracked domain raises ValueError
    with pytest.raises(ValueError, match="not in tracking list"):
        service.remove_domain("untracked.kulimi.tw")

    # Remove tracked domain without CF delete
    res = service.remove_domain("app.kulimi.tw", delete_record=False)
    assert res.removed_from_config is True
    assert res.record_deleted is False
    assert "app.kulimi.tw" not in service.config.records_to_update
    assert "rec-2" in fake_cf.records  # Still in CF

    # Remove with delete_record matching local IP
    res2 = service.remove_domain("kulimi.tw", delete_record=True)
    assert res2.removed_from_config is True
    assert res2.record_deleted is True
    assert "rec-1" not in fake_cf.records


def test_service_remove_domain_delete_record_safety(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    # api.kulimi.tw points to 5.6.7.8 (different from local 1.2.3.4)
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
    )

    res = service.remove_domain("api.kulimi.tw", delete_record=True)
    assert res.removed_from_config is True
    assert res.record_deleted is False
    assert "skipped deletion for safety" in (res.skip_reason or "")
    assert "rec-3" in fake_cf.records  # Untouched


def test_service_config_external_modification_and_error_handling(tmp_config_file: Path, fake_cf: FakeCloudflareClient) -> None:
    service = DDNSService(
        config_path=tmp_config_file,
        cf_factory=lambda c: fake_cf,
        ip_getter=lambda: "1.2.3.4",
    )

    # Valid modification
    cfg = json.loads(tmp_config_file.read_text())
    cfg["records_to_update"].append("external.kulimi.tw")
    tmp_config_file.write_text(json.dumps(cfg))

    reloaded = service.reload_config_if_changed()
    assert reloaded is True
    assert "external.kulimi.tw" in service.config.records_to_update

    # Broken modification: invalid JSON
    tmp_config_file.write_text("{ broken json")
    reloaded_broken = service.reload_config_if_changed()
    assert reloaded_broken is False
    # Old configuration preserved
    assert "external.kulimi.tw" in service.config.records_to_update
    assert service.last_error is not None
