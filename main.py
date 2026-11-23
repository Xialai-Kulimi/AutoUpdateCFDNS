import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading
import os
from dotenv import load_dotenv
import hashlib
from threading import Timer
import subprocess

load_dotenv()

from cloudflare import Cloudflare
from os import environ
import requests
from rich.console import Console
import json
from time import sleep
import sys

console = Console()

def log(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("dns_update.log", "a") as f:
        f.write(f"[{timestamp}] {msg}\n")
    console.log(msg)

config = {}
config_lock = threading.Lock()
last_hash = None
reload_timer = None
last_uploaded_ip = None

def load_config():
    global config, last_hash
    with open("config.json", "rb") as f:
        content = f.read()
        current_hash = hashlib.md5(content).hexdigest()
        if current_hash == last_hash:
            return  # No content change
        last_hash = current_hash
        with config_lock:
            config = json.loads(content.decode())
            log("🔁 config reloaded")
            log(str(config))

load_config()


cf = Cloudflare(
        api_email=environ.get("API_EMAIL"),
    # This is the default and can be omitted
    api_key=environ.get("API_KEY"),
    )



def do_dns_update(new_ip, ip_type):
    global last_uploaded_ip
    if new_ip == last_uploaded_ip:
        log(f"⏸ IP {new_ip} has not changed. Skipping update.")
        return
    
    zones = cf.dns.records.list(zone_id=config['zone_id'])
    dns_records = zones.to_dict()['result']
    total_scan = len(dns_records)
    total_update = len(config['records_to_update'])
    scan_count = 0
    update_count = 0
    for dns_record in dns_records:
        scan_count += 1
        if dns_record['name'] in config['records_to_update'] and dns_record['type'] == ip_type:
            if dns_record["content"] == new_ip:
                log(f"{dns_record['name']} {dns_record['type']} {dns_record['content']} is correct, no need to update")
                continue
            update_count += 1
            log(f'Update {dns_record["name"]} from {dns_record["type"]} {dns_record["content"]} to {ip_type} {new_ip}')
            dns_record['content'] = new_ip
            if config['debug']:
                log('[MOCK]')
                log(str(dns_record))
            else:
                # Always send proxied/ttl explicitly: omitting them makes Cloudflare reset proxied to false
                cf.dns.records.edit(zone_id = config['zone_id'], dns_record_id=dns_record['id'], type=dns_record['type'], content=dns_record['content'],  name=dns_record['name'], proxied=bool(dns_record.get('proxied', True)), ttl=dns_record.get('ttl', 1))
        # Print progress log at the end of each iteration
        log(f"✅ Scan complete: {scan_count}/{total_scan}, Updated: {update_count}/{total_update}")
    last_uploaded_ip = new_ip


# Add config monitor class and starter
class ConfigChangeHandler(FileSystemEventHandler):
    def on_modified(self, event):
        global reload_timer
        if event.src_path.endswith("config.json"):
            if reload_timer:
                reload_timer.cancel()
            reload_timer = Timer(0.5, load_config)
            reload_timer.start()

def start_config_monitor():
    event_handler = ConfigChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, ".", recursive=False)
    observer.start()
    return observer


if __name__ == "__main__":
    observer = start_config_monitor()
    try:
        if len(sys.argv) >= 2 and sys.argv[1].count(".") == 3:
            do_dns_update(sys.argv[1], 'A')
        else:
            while True:
                ip_cmd = os.environ.get("IP_COMMAND")
                if not ip_cmd:
                    log("⚠️ No IP_COMMAND set in environment")
                    sleep(30)
                    continue
                try:
                    result = subprocess.check_output(ip_cmd, shell=True, text=True).strip()
                    if result.count('.') == 3:
                        log(f"🌐 Detected IP: {result}")
                        do_dns_update(result, 'A')
                    else:
                        log(f"⚠️ Invalid IP format: {result}")
                except Exception as e:
                    log(f"❌ Failed to run IP_COMMAND: {e}")
                sleep(30)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
