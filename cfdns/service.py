from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable

from cfdns.cf_client import CloudflareClient, DNSRecord
from cfdns.config import (
    Config,
    config_fingerprint,
    load_config,
    normalize_record_name,
    save_config,
)

logger = logging.getLogger("cfdns.service")


@dataclass
class SyncResult:
    ip: str
    updated: list[str] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False


@dataclass
class AddResult:
    name: str
    action: str  # "created" | "updated" | "exists"
    ip: str
    proxied: bool
    ttl: int
    already_tracked: bool
    dry_run: bool = False


@dataclass
class RemoveResult:
    name: str
    removed_from_config: bool
    record_deleted: bool
    skip_reason: str | None = None
    dry_run: bool = False


def is_valid_hostname(hostname: str) -> bool:
    """Validate RFC 1035 / 1123 compliant hostname.

    Allows wildcard '*' as the leftmost label.
    Each label 1-63 chars [a-z0-9-], not starting or ending with hyphen.
    Total length <= 253 chars.
    """
    if not hostname or len(hostname) > 253:
        return False
    labels = hostname.split(".")
    for i, label in enumerate(labels):
        if i == 0 and label == "*":
            continue
        if not (1 <= len(label) <= 63):
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", label):
            return False
    return True


class DDNSService:
    """Core DDNS logic and state management."""

    def __init__(
        self,
        config_path: Path | str,
        cf_factory: Callable[[Config], CloudflareClient],
        ip_getter: Callable[[], str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config_path = Path(config_path).resolve()
        self.cf_factory = cf_factory
        self.ip_getter = ip_getter
        self.clock = clock

        self.lock = threading.RLock()
        self.config = load_config(self.config_path)
        self.last_fingerprint = config_fingerprint(self.config_path)

        self._cf_client: CloudflareClient | None = None
        self._cached_zone_name: str | None = None

        # State attributes protected by lock
        self.current_ip: str | None = None
        self.last_synced_ip: str | None = None
        self.last_check_at: float | None = None
        self.last_sync_at: float | None = None
        self.last_error: str | None = None
        self.started_at: float = self.clock()
        self.record_states: dict[str, str] = {}

    def _get_cf_client(self) -> CloudflareClient:
        """Get or initialize Cloudflare client instance."""
        if self._cf_client is None:
            self._cf_client = self.cf_factory(self.config)
        return self._cf_client

    def reload_config_if_changed(self) -> bool:
        """Reload configuration if the file has changed on disk.

        Retains previous configuration if loading or parsing fails.
        """
        with self.lock:
            try:
                current_fp = config_fingerprint(self.config_path)
            except Exception as e:
                self.last_error = f"Failed to check config fingerprint: {e}"
                logger.error("Failed to check config fingerprint: %s", e)
                return False

            if current_fp == self.last_fingerprint:
                return False

            try:
                new_cfg = load_config(self.config_path)
                self.config = new_cfg
                self.last_fingerprint = current_fp
                self._cf_client = None  # Invalidate client in case zone_id changed
                self._cached_zone_name = None
                logger.info("Configuration reloaded from %s", self.config_path)
                return True
            except Exception as e:
                self.last_error = f"Failed to reload config: {e}"
                logger.error("Failed to reload config: %s (keeping previous configuration)", e)
                return False

    def tick(self) -> None:
        """Periodic maintenance tick called by daemon."""
        with self.lock:
            self.reload_config_if_changed()

            try:
                ip = self.ip_getter()
                self.current_ip = ip
                self.last_check_at = self.clock()
            except Exception as e:
                self.last_error = f"Failed to get public IP: {e}"
                logger.warning("Failed to get public IP: %s", e)
                return

            needs_sync = (self.current_ip != self.last_synced_ip)
            reconcile_due = False
            if self.config.reconcile_interval > 0:
                if self.last_sync_at is None or (self.clock() - self.last_sync_at) >= self.config.reconcile_interval:
                    reconcile_due = True

            if needs_sync or reconcile_due:
                reason = "IP changed" if needs_sync else "reconcile interval reached"
                logger.debug("Triggering sync (%s)", reason)
                self.sync(ip=self.current_ip)
            else:
                logger.debug("IP %s has not changed and reconcile not due", self.current_ip)

    def sync(self, ip: str | None = None, force: bool = False) -> SyncResult:
        """Synchronize tracked DNS records with current IP."""
        with self.lock:
            if ip is None:
                try:
                    ip = self.ip_getter()
                    self.current_ip = ip
                    self.last_check_at = self.clock()
                except Exception as e:
                    self.last_error = f"Failed to get public IP: {e}"
                    raise
            else:
                self.current_ip = ip

            cf = self._get_cf_client()
            try:
                cf_records = cf.list_records(type="A")
            except Exception as e:
                self.last_error = f"Failed to query Cloudflare records: {e}"
                logger.error("%s", self.last_error)
                raise

            record_map = {r.name: r for r in cf_records}
            updated: list[str] = []
            ok: list[str] = []
            missing: list[str] = []
            errors: dict[str, str] = {}

            for domain in self.config.records_to_update:
                if domain not in record_map:
                    missing.append(domain)
                    self.record_states[domain] = "missing"
                    logger.warning("Tracked DNS record '%s' not found in Cloudflare zone", domain)
                    continue

                rec = record_map[domain]
                if rec.content == ip and not force:
                    ok.append(domain)
                    self.record_states[domain] = "ok"
                    continue

                # Record needs update
                if self.config.debug:
                    logger.info("[DRY-RUN] Update '%s' from %s to %s", domain, rec.content, ip)
                    updated.append(domain)
                    self.record_states[domain] = "updated"
                else:
                    try:
                        cf.update_record(rec, content=ip)
                        updated.append(domain)
                        self.record_states[domain] = "ok"
                        logger.info("Updated '%s' from %s to %s", domain, rec.content, ip)
                    except Exception as e:
                        errors[domain] = str(e)
                        self.record_states[domain] = "error"
                        logger.error("Failed to update record '%s': %s", domain, e)

            # Only update last_synced_ip if there were NO errors (missing is not an error)
            if not errors:
                self.last_synced_ip = ip
                self.last_sync_at = self.clock()
                self.last_error = None
            else:
                self.last_error = f"Sync had {len(errors)} error(s): {', '.join(errors.keys())}"

            dry_run = self.config.debug
            mode_tag = " [DRY-RUN]" if dry_run else ""
            logger.info(
                "Sync completed%s: IP=%s, updated=%d, ok=%d, missing=%d, errors=%d",
                mode_tag,
                ip,
                len(updated),
                len(ok),
                len(missing),
                len(errors),
            )

            return SyncResult(
                ip=ip,
                updated=updated,
                ok=ok,
                missing=missing,
                errors=errors,
                dry_run=dry_run,
            )

    def _resolve_and_validate_domain(self, name: str) -> str:
        """Resolve short domain or '@' to full domain name, and validate."""
        cf = self._get_cf_client()
        if self._cached_zone_name is None:
            self._cached_zone_name = cf.zone_name()
        zone_name = self._cached_zone_name

        norm = normalize_record_name(name)
        if norm == "@":
            resolved = zone_name
        elif not norm.endswith("." + zone_name) and norm != zone_name:
            if "." not in norm:
                resolved = f"{norm}.{zone_name}"
            else:
                raise ValueError(f"Domain '{name}' does not belong to zone '{zone_name}'")
        else:
            resolved = norm

        if not is_valid_hostname(resolved):
            raise ValueError(f"Invalid domain name or hostname syntax: '{resolved}'")

        return resolved

    def add_domain(
        self,
        name: str,
        proxied: bool | None = None,
        ttl: int | None = None,
        force: bool = False,
    ) -> AddResult:
        """Add domain to Cloudflare DNS and tracking list."""
        with self.lock:
            resolved_name = self._resolve_and_validate_domain(name)
            ip = self.current_ip or self.ip_getter()

            cf = self._get_cf_client()
            existing_records = cf.list_records(name=resolved_name)

            # Check for CNAME conflict
            cname_recs = [r for r in existing_records if r.type == "CNAME"]
            if cname_recs:
                raise ValueError(
                    f"A CNAME record already exists for '{resolved_name}'. "
                    f"A and CNAME records cannot coexist. Please remove the CNAME record first."
                )

            a_recs = [r for r in existing_records if r.type == "A"]
            target_proxied = proxied if proxied is not None else self.config.default_proxied
            target_ttl = ttl if ttl is not None else self.config.default_ttl
            dry_run = self.config.debug

            if a_recs:
                a_rec = a_recs[0]
                if a_rec.content == ip:
                    action = "exists"
                    target_proxied = a_rec.proxied
                    target_ttl = a_rec.ttl
                else:
                    if not force:
                        raise ValueError(
                            f"Record '{resolved_name}' currently points to {a_rec.content} (not local IP {ip}). "
                            f"To overwrite, use --force."
                        )
                    action = "updated"
                    if proxied is None:
                        target_proxied = a_rec.proxied
                    if ttl is None:
                        target_ttl = a_rec.ttl

                    if dry_run:
                        logger.info(
                            "[DRY-RUN] Update A record '%s' from %s to %s",
                            resolved_name,
                            a_rec.content,
                            ip,
                        )
                    else:
                        updated_rec = DNSRecord(
                            id=a_rec.id,
                            name=resolved_name,
                            type="A",
                            content=ip,
                            proxied=target_proxied,
                            ttl=target_ttl,
                        )
                        cf.update_record(updated_rec, content=ip)
            else:
                action = "created"
                if dry_run:
                    logger.info(
                        "[DRY-RUN] Create A record '%s' -> %s (proxied: %s, ttl: %s)",
                        resolved_name,
                        ip,
                        target_proxied,
                        target_ttl,
                    )
                else:
                    cf.create_record(
                        name=resolved_name,
                        type="A",
                        content=ip,
                        proxied=target_proxied,
                        ttl=target_ttl,
                    )

            # Add to tracking list in config (even in debug mode!)
            already_tracked = resolved_name in self.config.records_to_update
            if not already_tracked:
                self.config.records_to_update.append(resolved_name)
                save_config(self.config_path, self.config)
                self.last_fingerprint = config_fingerprint(self.config_path)
                logger.info("Added '%s' to tracked records in config", resolved_name)

            self.record_states[resolved_name] = "ok"

            return AddResult(
                name=resolved_name,
                action=action,
                ip=ip,
                proxied=target_proxied,
                ttl=target_ttl,
                already_tracked=already_tracked,
                dry_run=dry_run,
            )

    def remove_domain(self, name: str, delete_record: bool = False) -> RemoveResult:
        """Remove domain from tracking list, and optionally delete from Cloudflare."""
        with self.lock:
            resolved_name = self._resolve_and_validate_domain(name)
            if resolved_name not in self.config.records_to_update:
                raise ValueError(f"Domain '{resolved_name}' is not in tracking list")

            # Remove from config
            self.config.records_to_update.remove(resolved_name)
            save_config(self.config_path, self.config)
            self.last_fingerprint = config_fingerprint(self.config_path)
            self.record_states.pop(resolved_name, None)
            logger.info("Removed '%s' from tracking list in config", resolved_name)

            record_deleted = False
            skip_reason: str | None = None
            dry_run = self.config.debug

            if delete_record:
                cf = self._get_cf_client()
                a_recs = [r for r in cf.list_records(name=resolved_name, type="A")]
                current_ip = self.current_ip or self.ip_getter()

                if not a_recs:
                    skip_reason = "No matching A record found on Cloudflare"
                else:
                    matching = [r for r in a_recs if r.content == current_ip]
                    if not matching:
                        skip_reason = (
                            f"Record points to {a_recs[0].content}, not current local IP {current_ip}; "
                            "skipped deletion for safety"
                        )
                    else:
                        for rec in matching:
                            if dry_run:
                                logger.info("[DRY-RUN] Delete record '%s' (id: %s)", resolved_name, rec.id)
                                record_deleted = True
                            else:
                                cf.delete_record(rec.id)
                                record_deleted = True
                                logger.info("Deleted record '%s' (id: %s) on Cloudflare", resolved_name, rec.id)

            return RemoveResult(
                name=resolved_name,
                removed_from_config=True,
                record_deleted=record_deleted,
                skip_reason=skip_reason,
                dry_run=dry_run,
            )

    def status(self) -> dict[str, Any]:
        """Return current daemon and service status."""
        with self.lock:
            zone_name = None
            try:
                zone_name = self._get_cf_client().zone_name()
            except Exception:
                pass

            return {
                "version": "2.0.0",
                "pid": os.getpid(),
                "started_at": self.started_at,
                "uptime_s": int(self.clock() - self.started_at),
                "zone_id": self.config.zone_id,
                "zone_name": zone_name,
                "debug": self.config.debug,
                "interval": self.config.interval,
                "reconcile_interval": self.config.reconcile_interval,
                "current_ip": self.current_ip,
                "last_synced_ip": self.last_synced_ip,
                "last_check_at": self.last_check_at,
                "last_sync_at": self.last_sync_at,
                "last_error": self.last_error,
                "tracked_count": len(self.config.records_to_update),
                "config_path": str(self.config_path),
            }

    def list_records(self) -> list[dict[str, Any]]:
        """Query Cloudflare for real-time status of tracked records."""
        with self.lock:
            cf = self._get_cf_client()
            cf_records = cf.list_records(type="A")
            record_map = {r.name: r for r in cf_records}

            results: list[dict[str, Any]] = []
            for name in self.config.records_to_update:
                rec = record_map.get(name)
                if rec is None:
                    state = "missing"
                    content = None
                    proxied = None
                    ttl = None
                else:
                    content = rec.content
                    proxied = rec.proxied
                    ttl = rec.ttl
                    if self.current_ip is None:
                        state = "unknown"
                    elif rec.content == self.current_ip:
                        state = "ok"
                    else:
                        state = "stale"

                results.append({
                    "name": name,
                    "cf_content": content,
                    "proxied": proxied,
                    "ttl": ttl,
                    "state": state,
                })
            return results

    def get_config(self) -> dict[str, Any]:
        """Return current configuration, safe for display (no API_KEY value)."""
        with self.lock:
            return {
                "zone_id": self.config.zone_id,
                "records_to_update": list(self.config.records_to_update),
                "debug": self.config.debug,
                "interval": self.config.interval,
                "reconcile_interval": self.config.reconcile_interval,
                "default_ttl": self.config.default_ttl,
                "default_proxied": self.config.default_proxied,
                "extra": self.config.extra,
                "env": {
                    "API_EMAIL": bool(os.environ.get("API_EMAIL")),
                    "API_KEY": bool(os.environ.get("API_KEY")),
                    "IP_COMMAND": os.environ.get("IP_COMMAND"),
                },
            }
