from __future__ import annotations

from dataclasses import dataclass, field
import uuid
from typing import Any

from cfdns.cf_client import CloudflareError, DNSRecord


class FakeCloudflareClient:
    """In-memory mock for CloudflareClient."""

    def __init__(
        self,
        zone_id: str = "test-zone-id",
        zone_name: str = "kulimi.tw",
        records: list[DNSRecord] | None = None,
    ) -> None:
        self.zone_id = zone_id
        self._zone_name = zone_name
        self.records: dict[str, DNSRecord] = {}
        if records:
            for r in records:
                self.records[r.id] = r

        # Call log for assertions
        self.calls: list[dict[str, Any]] = []

        # Fault injection
        self.fail_on_update_names: set[str] = set()
        self.fail_on_create_names: set[str] = set()
        self.fail_on_delete_ids: set[str] = set()
        self.fail_on_list: bool = False

    def zone_name(self) -> str:
        self.calls.append({"method": "zone_name"})
        return self._zone_name

    def list_records(self, type: str | None = None, name: str | None = None) -> list[DNSRecord]:
        self.calls.append({"method": "list_records", "type": type, "name": name})
        if self.fail_on_list:
            raise CloudflareError("Injected failure on list_records")

        results: list[DNSRecord] = []
        for r in self.records.values():
            if type and r.type != type:
                continue
            if name and r.name != name:
                continue
            results.append(
                DNSRecord(
                    id=r.id,
                    name=r.name,
                    type=r.type,
                    content=r.content,
                    proxied=r.proxied,
                    ttl=r.ttl,
                )
            )
        return results

    def update_record(self, rec: DNSRecord, content: str) -> DNSRecord:
        self.calls.append({"method": "update_record", "id": rec.id, "name": rec.name, "content": content})
        if rec.name in self.fail_on_update_names:
            raise CloudflareError(f"Injected failure updating {rec.name}")

        updated = DNSRecord(
            id=rec.id,
            name=rec.name,
            type=rec.type,
            content=content,
            proxied=rec.proxied,
            ttl=rec.ttl,
        )
        self.records[rec.id] = updated
        return updated

    def create_record(self, name: str, type: str, content: str, proxied: bool, ttl: int) -> DNSRecord:
        self.calls.append({
            "method": "create_record",
            "name": name,
            "type": type,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        })
        if name in self.fail_on_create_names:
            raise CloudflareError(f"Injected failure creating {name}")

        new_id = f"rec-{uuid.uuid4().hex[:8]}"
        new_rec = DNSRecord(
            id=new_id,
            name=name,
            type=type,
            content=content,
            proxied=proxied,
            ttl=ttl,
        )
        self.records[new_id] = new_rec
        return new_rec

    def delete_record(self, record_id: str) -> None:
        self.calls.append({"method": "delete_record", "id": record_id})
        if record_id in self.fail_on_delete_ids:
            raise CloudflareError(f"Injected failure deleting {record_id}")
        self.records.pop(record_id, None)
