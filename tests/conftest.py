from __future__ import annotations

import json
from pathlib import Path
import pytest

from cfdns.cf_client import DNSRecord
from tests.fakes import FakeCloudflareClient


@pytest.fixture
def tmp_config_file(tmp_path: Path) -> Path:
    """Create a temporary config.json with sample content."""
    cfg_data = {
        "zone_id": "test-zone-12345",
        "records_to_update": ["kulimi.tw", "app.kulimi.tw", "api.kulimi.tw"],
        "debug": False,
        "interval": 30,
        "reconcile_interval": 3600,
        "default_ttl": 1,
        "default_proxied": False,
    }
    cfg_file = tmp_path / "config.json"
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg_data, f, indent=4)
        f.write("\n")
    return cfg_file


@pytest.fixture
def fake_cf() -> FakeCloudflareClient:
    """Create a fake CF client with initial test records."""
    records = [
        DNSRecord(id="rec-1", name="kulimi.tw", type="A", content="1.2.3.4", proxied=False, ttl=1),
        DNSRecord(id="rec-2", name="app.kulimi.tw", type="A", content="1.2.3.4", proxied=False, ttl=1),
        DNSRecord(id="rec-3", name="api.kulimi.tw", type="A", content="5.6.7.8", proxied=True, ttl=300),
    ]
    return FakeCloudflareClient(zone_id="test-zone-12345", zone_name="kulimi.tw", records=records)
