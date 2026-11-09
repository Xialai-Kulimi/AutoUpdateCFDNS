from __future__ import annotations

import ipaddress
import subprocess
import urllib.error
import urllib.request


class IPSourceError(Exception):
    """Raised when obtaining or validating a public IPv4 address fails."""


def validate_ipv4(s: str) -> str:
    """Validate that the string is a valid, globally routable IPv4 address.

    Rejects private, loopback, link-local, multicast, unspecified, and reserved IPs.
    Returns the normalized IPv4 address string.
    """
    cleaned = s.strip()
    try:
        ip = ipaddress.IPv4Address(cleaned)
    except ValueError as e:
        raise IPSourceError(f"'{cleaned}' is not a valid IPv4 address: {e}") from e

    if not ip.is_global or ip.is_multicast:
        reasons = []
        if ip.is_private:
            reasons.append("private")
        if ip.is_loopback:
            reasons.append("loopback")
        if ip.is_link_local:
            reasons.append("link-local")
        if ip.is_unspecified:
            reasons.append("unspecified")
        if ip.is_multicast:
            reasons.append("multicast")
        if ip.is_reserved:
            reasons.append("reserved")
        desc = ", ".join(reasons) if reasons else "non-global"
        raise IPSourceError(f"IPv4 address '{cleaned}' is not a public global address ({desc})")

    return str(ip)


def get_public_ipv4(ip_command: str | None = None, timeout: float = 10.0) -> str:
    """Obtain current public IPv4 address.

    If `ip_command` is provided, executes it in shell and extracts the first non-empty line.
    Otherwise falls back to querying https://1.1.1.1/cdn-cgi/trace.
    """
    if ip_command and ip_command.strip():
        cmd = ip_command.strip()
        try:
            res = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise IPSourceError(f"IP command timed out after {timeout}s: '{cmd}'") from e
        except OSError as e:
            raise IPSourceError(f"Failed to execute IP command '{cmd}': {e}") from e

        if res.returncode != 0:
            err = res.stderr.strip() or f"exit code {res.returncode}"
            raise IPSourceError(f"IP command failed ({err}): '{cmd}'")

        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        if not lines:
            raise IPSourceError(f"IP command produced no output: '{cmd}'")

        return validate_ipv4(lines[0])

    # Fallback to Cloudflare trace endpoint
    url = "https://1.1.1.1/cdn-cgi/trace"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "cfdns/2.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise IPSourceError(f"Failed to fetch IP from {url}: {e}") from e
    except TimeoutError as e:
        raise IPSourceError(f"Timeout fetching IP from {url} after {timeout}s") from e
    except Exception as e:
        raise IPSourceError(f"Unexpected error fetching IP from {url}: {e}") from e

    ip_val = None
    for line in content.splitlines():
        if line.startswith("ip="):
            ip_val = line[len("ip="):].strip()
            break

    if not ip_val:
        raise IPSourceError(f"Could not find 'ip=' in response from {url}")

    return validate_ipv4(ip_val)
