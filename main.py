from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading
import os
from dotenv import load_dotenv

load_dotenv()

from cloudflare import Cloudflare
from os import environ
import requests
from rich.console import Console
import json
from time import sleep
from rich.progress import Progress
import sys

console = Console()

config = {}
config_lock = threading.Lock()

def load_config():
    global config
    with open("config.json") as f:
        with config_lock:
            config = json.load(f)
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
    with Progress(console=console) as progress:
        task_scan = progress.add_task("Scanning DNS...", total=len(dns_records))
        task_update = progress.add_task("Updating DNS...", total=len(config['records_to_update']))
        for dns_record in dns_records:
            progress.update(task_scan, advance=1)
            if dns_record['name'] in config['records_to_update'] and dns_record['type'] ==ip_type:
                if dns_record["content"] == new_ip:
                    console.log(f"{dns_record['name']} {dns_record['type']} {dns_record['content']} is correct, no need to update")
                    continue
                progress.update(task_update, advance=1)
                console.log(f'Update {dns_record["name"]} from {dns_record["type"]} {dns_record["content"]} to {ip_type} {new_ip}')
                dns_record['content'] = new_ip
                if config['debug']:
                    console.log('[MOCK]')
                    console.log(dns_record)
                else:
                    cf.dns.records.edit(zone_id = config['zone_id'], dns_record_id=dns_record['id'], type=dns_record['type'], content=dns_record['content'],  name=dns_record['name'])


# Add config monitor class and starter
class ConfigChangeHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith("config.json"):
            try:
                load_config()
            except Exception as e:
                console.log(f"❌ Failed to reload config: {e}")

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
