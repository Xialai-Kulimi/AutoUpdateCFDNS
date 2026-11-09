from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import cfdns
from cfdns.config import ConfigError, load_config, load_env
from cfdns.ipsource import IPSourceError, validate_ipv4
from cfdns.paths import config_path as default_config_path, env_path, socket_path as default_socket_path
from cfdns.protocol import DaemonNotRunning, send_request

console = Console()
err_console = Console(stderr=True)


def format_timestamp(ts: float | None) -> str:
    """Format unix timestamp into human-readable local time."""
    if ts is None:
        return "never"
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def handle_daemon(args: argparse.Namespace) -> int:
    """Run daemon in foreground."""
    from cfdns.daemon import run_daemon

    run_daemon(config_path=args.config, socket_path=args.socket)
    return 0


def handle_status(args: argparse.Namespace) -> int:
    """Query and display daemon status."""
    try:
        resp = send_request("status", socket_path=args.socket)
    except DaemonNotRunning as e:
        err_console.print(f"[red]Error:[/red] daemon 未執行 ({e})")
        return 3

    if not resp.get("ok"):
        err_console.print(f"[red]Error:[/red] {resp.get('error')}")
        return 1

    data = resp["data"]
    if args.json:
        console.print_json(json.dumps(data, ensure_ascii=False))
        return 0

    table = Table(title="cfdns Daemon Status", show_header=False, box=None)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value")

    table.add_row("Version", data.get("version", ""))
    table.add_row("PID", str(data.get("pid", "")))
    table.add_row("Status", "[green]running[/green]")
    table.add_row("Started At", format_timestamp(data.get("started_at")))
    uptime_s = data.get("uptime_s", 0)
    table.add_row("Uptime", f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m {uptime_s % 60}s")
    table.add_row("Zone Name", data.get("zone_name") or "[dim]unknown[/dim]")
    table.add_row("Zone ID", data.get("zone_id", ""))
    table.add_row("Debug Mode", "[yellow]true (DRY-RUN)[/yellow]" if data.get("debug") else "false")
    table.add_row("Interval", f"{data.get('interval')}s")
    table.add_row("Reconcile Interval", f"{data.get('reconcile_interval')}s" if data.get("reconcile_interval") else "disabled")
    table.add_row("Current Public IP", data.get("current_ip") or "[yellow]unknown[/yellow]")
    table.add_row("Last Synced IP", data.get("last_synced_ip") or "[yellow]never[/yellow]")
    table.add_row("Last Check At", format_timestamp(data.get("last_check_at")))
    table.add_row("Last Sync At", format_timestamp(data.get("last_sync_at")))
    table.add_row("Tracked Domains", str(data.get("tracked_count", 0)))
    table.add_row("Config Path", data.get("config_path", ""))
    last_err = data.get("last_error")
    table.add_row("Last Error", f"[red]{last_err}[/red]" if last_err else "[green]none[/green]")

    console.print(Panel(table, border_style="cyan"))
    return 0


def handle_config(args: argparse.Namespace) -> int:
    """Display current configuration."""
    cfg_path = default_config_path(args.config)
    try:
        resp = send_request("config", socket_path=args.socket)
        if not resp.get("ok"):
            err_console.print(f"[red]Error:[/red] {resp.get('error')}")
            return 1
        source = "daemon"
        data = resp.get("data")
    except DaemonNotRunning:
        source = "file"
        # Load .env next to the config so the env presence report is accurate
        load_env(env_path(cfg_path))
        try:
            cfg = load_config(cfg_path)
            data = {
                "zone_id": cfg.zone_id,
                "records_to_update": cfg.records_to_update,
                "debug": cfg.debug,
                "interval": cfg.interval,
                "reconcile_interval": cfg.reconcile_interval,
                "default_ttl": cfg.default_ttl,
                "default_proxied": cfg.default_proxied,
                "extra": cfg.extra,
                "env": {
                    "API_EMAIL": bool(os.environ.get("API_EMAIL")),
                    "API_KEY": bool(os.environ.get("API_KEY")),
                    "IP_COMMAND": os.environ.get("IP_COMMAND"),
                },
            }
        except ConfigError as e:
            err_console.print(f"[red]Error reading offline config:[/red] {e}")
            return 1

    if args.json:
        console.print_json(json.dumps(data, ensure_ascii=False))
        return 0

    note = "" if source == "daemon" else " [yellow](daemon 未執行，此為本地檔案內容)[/yellow]"
    table = Table(title=f"cfdns Configuration{note}", show_header=False, box=None)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")

    table.add_row("Zone ID", data.get("zone_id", ""))
    table.add_row("Debug Mode", str(data.get("debug")))
    table.add_row("Interval", f"{data.get('interval')}s")
    table.add_row("Reconcile Interval", f"{data.get('reconcile_interval')}s")
    table.add_row("Default TTL", str(data.get("default_ttl")))
    table.add_row("Default Proxied", str(data.get("default_proxied")))
    table.add_row("Tracked Domains", ", ".join(data.get("records_to_update", [])) or "[dim]none[/dim]")

    env_info = data.get("env", {})
    table.add_row("Env: API_EMAIL Set", "[green]yes[/green]" if env_info.get("API_EMAIL") else "[red]no[/red]")
    table.add_row("Env: API_KEY Set", "[green]yes[/green]" if env_info.get("API_KEY") else "[red]no[/red]")
    table.add_row("Env: IP_COMMAND", env_info.get("IP_COMMAND") or "[dim]none[/dim]")

    if data.get("extra"):
        table.add_row("Extra Fields", json.dumps(data.get("extra"), ensure_ascii=False))

    console.print(Panel(table, border_style="cyan"))
    return 0


def handle_list(args: argparse.Namespace) -> int:
    """List tracked domains and real Cloudflare DNS status."""
    try:
        resp = send_request("list", socket_path=args.socket)
    except DaemonNotRunning as e:
        err_console.print(f"[red]Error:[/red] daemon 未執行 ({e})")
        return 3

    if not resp.get("ok"):
        err_console.print(f"[red]Error:[/red] {resp.get('error')}")
        return 1

    records = resp.get("data", [])
    if args.json:
        console.print_json(json.dumps(records, ensure_ascii=False))
        return 0

    table = Table(title="Tracked Cloudflare DNS Records")
    table.add_column("Record Name", style="bold")
    table.add_column("Cloudflare IP")
    table.add_column("Proxied")
    table.add_column("TTL")
    table.add_column("Status")

    for r in records:
        state = r.get("state", "unknown")
        if state == "ok":
            status_str = "[green]ok[/green]"
        elif state == "stale":
            status_str = "[yellow]stale (IP mismatch)[/yellow]"
        elif state == "missing":
            status_str = "[red]missing on CF[/red]"
        else:
            status_str = f"[dim]{state}[/dim]"

        proxied_str = "[orange3]yes[/orange3]" if r.get("proxied") else "no"
        ttl_val = r.get("ttl")
        ttl_str = "Auto" if ttl_val == 1 else str(ttl_val or "-")
        content_str = r.get("cf_content") or "[dim]-[/dim]"

        table.add_row(r.get("name"), content_str, proxied_str, ttl_str, status_str)

    console.print(table)
    return 0


def handle_add(args: argparse.Namespace) -> int:
    """Add a domain to tracking and create/update Cloudflare record."""
    payload = {
        "name": args.name,
        "proxied": args.proxied,
        "ttl": args.ttl,
        "force": args.force,
    }
    try:
        resp = send_request("add", args=payload, socket_path=args.socket)
    except DaemonNotRunning as e:
        err_console.print(f"[red]Error:[/red] daemon 未執行 ({e})")
        return 3

    if not resp.get("ok"):
        err_console.print(f"[red]Error:[/red] {resp.get('error')}")
        return 1

    data = resp.get("data", {})
    if args.json:
        console.print_json(json.dumps(data, ensure_ascii=False))
        return 0

    prefix = "[yellow][DRY-RUN][/yellow] " if data.get("dry_run") else ""
    action = data.get("action")
    name = data.get("name")
    ip = data.get("ip")
    proxied = "yes" if data.get("proxied") else "no"
    ttl = data.get("ttl")

    if action == "created":
        console.print(f"{prefix}✔ Created A record {name} → {ip} (proxied: {proxied}, ttl: {ttl}) and added to tracking")
    elif action == "updated":
        console.print(f"{prefix}✔ Updated A record {name} → {ip} (proxied: {proxied}, ttl: {ttl}) and added to tracking")
    elif action == "exists":
        tracking = "already tracked" if data.get("already_tracked") else "added to tracking"
        console.print(f"{prefix}✔ A record {name} already points to {ip}; {tracking}")

    if data.get("dry_run"):
        console.print("[dim](Note: config.json was updated locally, Cloudflare API call was skipped due to debug=true)[/dim]")

    return 0


def handle_remove(args: argparse.Namespace) -> int:
    """Remove a domain from tracking."""
    if args.delete_record and not args.yes:
        confirm = input(f"Are you sure you want to delete the Cloudflare DNS record for '{args.name}'? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            console.print("Operation cancelled.")
            return 0

    payload = {
        "name": args.name,
        "delete_record": args.delete_record,
    }
    try:
        resp = send_request("remove", args=payload, socket_path=args.socket)
    except DaemonNotRunning as e:
        err_console.print(f"[red]Error:[/red] daemon 未執行 ({e})")
        return 3

    if not resp.get("ok"):
        err_console.print(f"[red]Error:[/red] {resp.get('error')}")
        return 1

    data = resp.get("data", {})
    if args.json:
        console.print_json(json.dumps(data, ensure_ascii=False))
        return 0

    prefix = "[yellow][DRY-RUN][/yellow] " if data.get("dry_run") else ""
    name = data.get("name")

    msg = f"{prefix}✔ Removed '{name}' from tracking list"
    if args.delete_record:
        if data.get("record_deleted"):
            msg += " and deleted A record on Cloudflare"
        elif data.get("skip_reason"):
            msg += f" (Cloudflare record was not deleted: {data.get('skip_reason')})"

    console.print(msg)
    return 0


def handle_sync(args: argparse.Namespace) -> int:
    """Trigger immediate DNS sync."""
    if args.ip:
        try:
            validated_ip = validate_ipv4(args.ip)
        except IPSourceError as e:
            err_console.print(f"[red]Invalid IP address:[/red] {e}")
            return 1
    else:
        validated_ip = None

    try:
        resp = send_request("sync", args={"ip": validated_ip}, socket_path=args.socket)
    except DaemonNotRunning as e:
        err_console.print(f"[red]Error:[/red] daemon 未執行 ({e})")
        return 3

    if not resp.get("ok"):
        err_console.print(f"[red]Error:[/red] {resp.get('error')}")
        return 1

    data = resp.get("data", {})
    if args.json:
        console.print_json(json.dumps(data, ensure_ascii=False))
        return 0

    prefix = "[yellow][DRY-RUN][/yellow] " if data.get("dry_run") else ""
    ip = data.get("ip")
    updated = data.get("updated", [])
    ok = data.get("ok", [])
    missing = data.get("missing", [])
    errors = data.get("errors", {})

    console.print(f"{prefix}✔ Sync finished for IP {ip}:")
    if updated:
        console.print(f"  - Updated ({len(updated)}): {', '.join(updated)}")
    if ok:
        console.print(f"  - Up-to-date ({len(ok)}): {', '.join(ok)}")
    if missing:
        console.print(f"  - [yellow]Missing on CF ({len(missing)}):[/yellow] {', '.join(missing)}")
    if errors:
        console.print(f"  - [red]Errors ({len(errors)}):[/red]")
        for d, err in errors.items():
            console.print(f"      {d}: {err}")
        return 1

    return 0


def handle_logs(args: argparse.Namespace) -> int:
    """View daemon logs via journalctl."""
    cmd = ["journalctl", "-u", "cfdns"]
    if args.lines:
        cmd.extend(["-n", str(args.lines)])
    if args.follow:
        cmd.append("-f")

    try:
        os.execvp("journalctl", cmd)
    except OSError as e:
        err_console.print(f"[red]Failed to execute journalctl:[/red] {e}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="cfdns",
        description="Cloudflare DDNS background service and CLI",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"cfdns {cfdns.__version__}",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=None,
        help="Path to cfdns Unix domain socket (default: /run/cfdns/cfdns.sock or $CFDNS_SOCKET)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.json (default: config.json or $CFDNS_CONFIG)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON response",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # daemon
    subparsers.add_parser("daemon", help="Run cfdns daemon in foreground (used by systemd)")

    # status
    subparsers.add_parser("status", help="Show daemon status")

    # config
    subparsers.add_parser("config", help="Show current configuration")

    # list
    subparsers.add_parser("list", help="List tracked domains and real Cloudflare DNS status")

    # add
    add_parser = subparsers.add_parser("add", help="Add a domain to tracking and update Cloudflare")
    add_parser.add_argument("name", help="Domain or subdomain name (e.g., 'app' or 'app.example.com')")
    proxied_group = add_parser.add_mutually_exclusive_group()
    proxied_group.add_argument(
        "--proxied",
        action="store_true",
        default=None,
        dest="proxied",
        help="Enable Cloudflare proxy (orange cloud)",
    )
    proxied_group.add_argument(
        "--no-proxied",
        action="store_false",
        dest="proxied",
        help="Disable Cloudflare proxy (DNS only)",
    )
    add_parser.add_argument("--ttl", type=int, default=None, help="DNS TTL in seconds (1 = Auto)")
    add_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite record even if it points to a different IP",
    )

    # remove
    rem_parser = subparsers.add_parser("remove", help="Remove a domain from tracking")
    rem_parser.add_argument("name", help="Domain name to remove")
    rem_parser.add_argument(
        "--delete-record",
        action="store_true",
        help="Also delete the A record on Cloudflare if pointing to local IP",
    )
    rem_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Confirm deletion without prompting",
    )

    # sync
    sync_parser = subparsers.add_parser("sync", help="Trigger immediate DNS synchronization")
    sync_parser.add_argument("--ip", type=str, default=None, help="Specify IPv4 address manually (used by ppp hook)")

    # logs
    logs_parser = subparsers.add_parser("logs", help="View daemon logs via journalctl")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    logs_parser.add_argument("-n", "--lines", type=int, default=50, help="Number of lines to show (default: 50)")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "daemon": handle_daemon,
        "status": handle_status,
        "config": handle_config,
        "list": handle_list,
        "add": handle_add,
        "remove": handle_remove,
        "sync": handle_sync,
        "logs": handle_logs,
    }

    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 2

    try:
        return handler(args)
    except Exception as e:
        if os.environ.get("CFDNS_DEBUG") == "1":
            raise
        err_console.print(f"[red]Error:[/red] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
