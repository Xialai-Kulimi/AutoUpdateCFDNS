from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from cloudflare import Cloudflare

from cfdns.config import ConfigError


class CloudflareError(Exception):
    """Raised when a Cloudflare API call fails."""


@dataclass
class DNSRecord:
    id: str
    name: str
    type: str
    content: str
    proxied: bool
    ttl: int


class CloudflareClient:
    """Wrapper around Cloudflare SDK v3.1.0."""

    def __init__(self, api_email: str | None, api_key: str | None, zone_id: str) -> None:
        if not api_email or not api_email.strip():
            raise ConfigError("Missing Cloudflare API_EMAIL credential")
        if not api_key or not api_key.strip():
            raise ConfigError("Missing Cloudflare API_KEY credential")
        if not zone_id or not zone_id.strip():
            raise ConfigError("Missing Cloudflare zone_id")

        self.api_email = api_email.strip()
        self.api_key = api_key.strip()
        self.zone_id = zone_id.strip()
        self._cf = Cloudflare(api_email=self.api_email, api_key=self.api_key)
        self._cached_zone_name: str | None = None

    def zone_name(self) -> str:
        """Fetch and cache zone apex name."""
        if self._cached_zone_name is None:
            try:
                zone = self._cf.zones.get(zone_id=self.zone_id)
                if not zone or not zone.name:
                    raise CloudflareError(f"Zone ID {self.zone_id} not found or has no name")
                self._cached_zone_name = zone.name.lower().rstrip(".")
            except CloudflareError:
                raise
            except Exception as e:
                raise CloudflareError(f"Failed to fetch zone information: {e}") from e
        return self._cached_zone_name

    def list_records(self, type: str | None = None, name: str | None = None) -> list[DNSRecord]:
        """List DNS records in the zone.

        Auto-paginates across all pages returned by the Cloudflare SDK.
        """
        try:
            kwargs: dict[str, Any] = {"zone_id": self.zone_id}
            if type:
                kwargs["type"] = type
            if name:
                kwargs["name"] = name

            pager = self._cf.dns.records.list(**kwargs)
            records: list[DNSRecord] = []
            for r in pager:
                rec_name = str(r.name).lower().rstrip(".")
                rec_type = str(r.type)
                rec_content = str(r.content) if r.content is not None else ""
                rec_proxied = bool(r.proxied) if r.proxied is not None else False
                rec_ttl = int(r.ttl) if r.ttl is not None else 1
                records.append(
                    DNSRecord(
                        id=str(r.id),
                        name=rec_name,
                        type=rec_type,
                        content=rec_content,
                        proxied=rec_proxied,
                        ttl=rec_ttl,
                    )
                )
            return records
        except Exception as e:
            raise CloudflareError(f"Failed to list DNS records: {e}") from e

    def update_record(self, rec: DNSRecord, content: str) -> DNSRecord:
        """Update an existing DNS record, preserving name, type, proxied, and ttl."""
        try:
            r = self._cf.dns.records.edit(
                rec.id,
                zone_id=self.zone_id,
                name=rec.name,
                type=rec.type,  # type: ignore
                content=content,
                proxied=rec.proxied,
                ttl=rec.ttl,
            )
            return DNSRecord(
                id=str(r.id),
                name=str(r.name).lower().rstrip("."),
                type=str(r.type),
                content=str(r.content) if r.content is not None else "",
                proxied=bool(r.proxied) if r.proxied is not None else False,
                ttl=int(r.ttl) if r.ttl is not None else 1,
            )
        except Exception as e:
            raise CloudflareError(f"Failed to update DNS record '{rec.name}' ({rec.id}): {e}") from e

    def create_record(self, name: str, type: str, content: str, proxied: bool, ttl: int) -> DNSRecord:
        """Create a new DNS record in Cloudflare."""
        try:
            r = self._cf.dns.records.create(
                zone_id=self.zone_id,
                name=name,
                type=type,  # type: ignore
                content=content,
                proxied=proxied,
                ttl=ttl,
            )
            return DNSRecord(
                id=str(r.id),
                name=str(r.name).lower().rstrip("."),
                type=str(r.type),
                content=str(r.content) if r.content is not None else "",
                proxied=bool(r.proxied) if r.proxied is not None else False,
                ttl=int(r.ttl) if r.ttl is not None else 1,
            )
        except Exception as e:
            raise CloudflareError(f"Failed to create DNS record '{name}': {e}") from e

    def delete_record(self, record_id: str) -> None:
        """Delete a DNS record from Cloudflare."""
        try:
            self._cf.dns.records.delete(record_id, zone_id=self.zone_id)
        except Exception as e:
            raise CloudflareError(f"Failed to delete DNS record {record_id}: {e}") from e
