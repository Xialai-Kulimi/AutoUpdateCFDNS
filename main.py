from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading
import os
from dotenv import load_dotenv
import hashlib
from threading import Timer

load_dotenv()

from cloudflare import Cloudflare
from os import environ
import requests
from rich.console import Console
import json
from time import sleep
import sys

console = Console()

config = {}
config_lock = threading.Lock()
last_hash = None
reload_timer = None

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
            console.log("🔁 config reloaded")
            console.log(config)

load_config()


cf = Cloudflare(
        api_email=environ.get("API_EMAIL"),
    # This is the default and can be omitted
    api_key=environ.get("API_KEY"),
    )



def do_dns_update(new_ip, ip_type):
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
                console.log(f"{dns_record['name']} {dns_record['type']} {dns_record['content']} is correct, no need to update")
                continue
            update_count += 1
            console.log(f'Update {dns_record["name"]} from {dns_record["type"]} {dns_record["content"]} to {ip_type} {new_ip}')
            dns_record['content'] = new_ip
            if config['debug']:
                console.log('[MOCK]')
                console.log(dns_record)
            else:
                cf.dns.records.edit(zone_id = config['zone_id'], dns_record_id=dns_record['id'], type=dns_record['type'], content=dns_record['content'],  name=dns_record['name'])
        # Print progress log at the end of each iteration
        console.log(f"✅ Scan complete: {scan_count}/{total_scan}, Updated: {update_count}/{total_update}")


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
        do_dns_update(sys.argv[0], 'A')
        while True:
            sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
