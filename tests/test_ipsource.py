from __future__ import annotations

import io
from unittest.mock import MagicMock, patch
import urllib.error
import pytest

from cfdns.ipsource import IPSourceError, get_public_ipv4, validate_ipv4


def test_validate_ipv4_valid() -> None:
    assert validate_ipv4("1.1.1.1") == "1.1.1.1"
    assert validate_ipv4("  8.8.8.8 \n") == "8.8.8.8"
    assert validate_ipv4("140.112.30.26") == "140.112.30.26"


def test_validate_ipv4_rejects_non_global() -> None:
    # Private RFC 1918
    with pytest.raises(IPSourceError, match="not a public global address.*private"):
        validate_ipv4("192.168.1.1")
    with pytest.raises(IPSourceError, match="not a public global address.*private"):
        validate_ipv4("10.0.0.1")
    with pytest.raises(IPSourceError, match="not a public global address.*private"):
        validate_ipv4("172.16.0.1")

    # Loopback
    with pytest.raises(IPSourceError, match="loopback"):
        validate_ipv4("127.0.0.1")

    # Link-local
    with pytest.raises(IPSourceError, match="link-local"):
        validate_ipv4("169.254.1.1")

    # Unspecified
    with pytest.raises(IPSourceError, match="unspecified"):
        validate_ipv4("0.0.0.0")

    # Multicast
    with pytest.raises(IPSourceError, match="multicast"):
        validate_ipv4("224.0.0.1")


def test_validate_ipv4_rejects_malformed() -> None:
    # Invalid strings
    with pytest.raises(IPSourceError, match="not a valid IPv4 address"):
        validate_ipv4("not-an-ip")
    with pytest.raises(IPSourceError, match="not a valid IPv4 address"):
        validate_ipv4("256.1.1.1")
    with pytest.raises(IPSourceError, match="not a valid IPv4 address"):
        validate_ipv4("1.2.3")
    with pytest.raises(IPSourceError, match="not a valid IPv4 address"):
        validate_ipv4("1.2.3.4.5")
    with pytest.raises(IPSourceError, match="not a valid IPv4 address"):
        validate_ipv4("2001:db8::1")  # IPv6 not accepted


def test_get_public_ipv4_command_success() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="\n1.1.1.1\n", stderr="")
        ip = get_public_ipv4("dummy_cmd")
        assert ip == "1.1.1.1"


def test_get_public_ipv4_command_failure_exit_code() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="command failed")
        with pytest.raises(IPSourceError, match="IP command failed"):
            get_public_ipv4("dummy_cmd")


def test_get_public_ipv4_command_invalid_output() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="invalid_ip_text", stderr="")
        with pytest.raises(IPSourceError, match="not a valid IPv4 address"):
            get_public_ipv4("dummy_cmd")


def test_get_public_ipv4_command_empty_output() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="   \n\n", stderr="")
        with pytest.raises(IPSourceError, match="produced no output"):
            get_public_ipv4("dummy_cmd")


def test_get_public_ipv4_fallback_trace() -> None:
    trace_body = "fl=123\nh=1.1.1.1\nip=1.1.1.1\nts=123456\n".encode("utf-8")
    mock_response = io.BytesIO(trace_body)

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value = mock_response
        ip = get_public_ipv4(ip_command=None)
        assert ip == "1.1.1.1"


def test_get_public_ipv4_fallback_network_error() -> None:
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(IPSourceError, match="Failed to fetch IP"):
            get_public_ipv4(ip_command=None)
