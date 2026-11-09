from __future__ import annotations

import json
from pathlib import Path
import pytest

from cfdns.config import (
    Config,
    ConfigError,
    config_fingerprint,
    load_config,
    normalize_record_name,
    save_config,
)


def test_normalize_record_name() -> None:
    assert normalize_record_name("  Kulimi.TW.  ") == "kulimi.tw"
    assert normalize_record_name("APP.example.COM") == "app.example.com"
    assert normalize_record_name("@") == "@"


def test_load_legacy_config_compatibility(tmp_path: Path) -> None:
    legacy_file = tmp_path / "legacy_config.json"
    legacy_file.write_text(
        json.dumps({
            "zone_id": "legacy_zone_abc",
            "debug": False,
            "records_to_update": ["KULIMI.TW.", "raw.kulimi.tw", "kulimi.tw"],
        })
    )

    cfg = load_config(legacy_file)
    assert cfg.zone_id == "legacy_zone_abc"
    assert cfg.debug is False
    # Check deduplication and normalization
    assert cfg.records_to_update == ["kulimi.tw", "raw.kulimi.tw"]
    # Check default values
    assert cfg.interval == 30
    assert cfg.reconcile_interval == 3600
    assert cfg.default_ttl == 1
    assert cfg.default_proxied is False


def test_preserve_unknown_fields(tmp_path: Path) -> None:
    cfg_file = tmp_path / "extra_config.json"
    cfg_file.write_text(
        json.dumps({
            "zone_id": "zone_123",
            "records_to_update": ["foo.bar"],
            "custom_field": "hello",
            "another_nested": {"a": 1, "b": True},
        })
    )

    cfg = load_config(cfg_file)
    assert cfg.extra == {
        "custom_field": "hello",
        "another_nested": {"a": 1, "b": True},
    }

    # Save and verify unknown fields are retained
    save_config(cfg_file, cfg)
    with open(cfg_file, "r", encoding="utf-8") as f:
        reloaded = json.load(f)

    assert reloaded["custom_field"] == "hello"
    assert reloaded["another_nested"] == {"a": 1, "b": True}
    assert reloaded["zone_id"] == "zone_123"


def test_atomic_write_and_permissions(tmp_path: Path) -> None:
    cfg_file = tmp_path / "atomic_config.json"
    cfg = Config(
        zone_id="test_zone",
        records_to_update=["a.kulimi.tw", "b.kulimi.tw"],
        debug=True,
        interval=10,
    )

    save_config(cfg_file, cfg)
    assert cfg_file.is_file()

    # Re-read
    loaded = load_config(cfg_file)
    assert loaded.zone_id == "test_zone"
    assert loaded.debug is True
    assert loaded.interval == 10
    assert loaded.records_to_update == ["a.kulimi.tw", "b.kulimi.tw"]


def test_invalid_configs_raise_config_error(tmp_path: Path) -> None:
    # Missing file
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "nonexistent.json")

    # Malformed JSON
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{ this is not json }")
    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_config(bad_json)

    # Missing zone_id
    no_zone = tmp_path / "no_zone.json"
    no_zone.write_text(json.dumps({"records_to_update": ["test.com"]}))
    with pytest.raises(ConfigError, match="Invalid or missing 'zone_id'"):
        load_config(no_zone)

    # Empty zone_id
    empty_zone = tmp_path / "empty_zone.json"
    empty_zone.write_text(json.dumps({"zone_id": "   ", "records_to_update": ["test.com"]}))
    with pytest.raises(ConfigError, match="Invalid or missing 'zone_id'"):
        load_config(empty_zone)

    # Invalid interval (< 5)
    bad_interval = tmp_path / "bad_interval.json"
    bad_interval.write_text(json.dumps({"zone_id": "z1", "records_to_update": [], "interval": 4}))
    with pytest.raises(ConfigError, match="'interval' must be an integer >= 5"):
        load_config(bad_interval)

    # records_to_update not a list
    bad_records = tmp_path / "bad_records.json"
    bad_records.write_text(json.dumps({"zone_id": "z1", "records_to_update": "not a list"}))
    with pytest.raises(ConfigError, match="'records_to_update' must be a list"):
        load_config(bad_records)


def test_config_fingerprint(tmp_path: Path) -> None:
    cfg_file = tmp_path / "fp_test.json"
    cfg_file.write_text('{"zone_id": "z"}')
    fp1 = config_fingerprint(cfg_file)
    assert isinstance(fp1, str) and len(fp1) == 64

    # Modifying changes fingerprint
    cfg_file.write_text('{"zone_id": "z2"}')
    fp2 = config_fingerprint(cfg_file)
    assert fp1 != fp2
