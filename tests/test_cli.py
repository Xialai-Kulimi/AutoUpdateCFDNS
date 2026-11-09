from __future__ import annotations

import json
from pathlib import Path
import pytest

from cfdns.cli import main


def test_cli_help(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Cloudflare DDNS background service and CLI" in captured.out


def test_cli_version(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "cfdns 2.0.0" in captured.out


def test_cli_daemon_not_running_status_exit_3(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    nonexistent_sock = tmp_path / "cfdns.sock"
    code = main(["--socket", str(nonexistent_sock), "status"])
    assert code == 3
    captured = capsys.readouterr()
    assert "daemon 未執行" in captured.err


def test_cli_daemon_not_running_list_exit_3(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    nonexistent_sock = tmp_path / "cfdns.sock"
    code = main(["--socket", str(nonexistent_sock), "list"])
    assert code == 3
    captured = capsys.readouterr()
    assert "daemon 未執行" in captured.err


def test_cli_config_fallback_when_daemon_not_running(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    nonexistent_sock = tmp_path / "cfdns.sock"
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "zone_id": "fallback_zone",
        "records_to_update": ["foo.kulimi.tw"],
        "debug": True,
    }))

    code = main(["--socket", str(nonexistent_sock), "--config", str(cfg_file), "config"])
    assert code == 0
    captured = capsys.readouterr()
    assert "fallback_zone" in captured.out
    assert "daemon 未執行" in captured.out or "本地檔案" in captured.out


def test_cli_config_json_fallback(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    nonexistent_sock = tmp_path / "cfdns.sock"
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "zone_id": "fallback_zone",
        "records_to_update": ["foo.kulimi.tw"],
        "debug": True,
    }))

    code = main(["--socket", str(nonexistent_sock), "--config", str(cfg_file), "--json", "config"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["zone_id"] == "fallback_zone"
