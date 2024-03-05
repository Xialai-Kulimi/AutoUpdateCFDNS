from dotenv import load_dotenv

load_dotenv()

import CloudFlare
from os import environ
import requests
from rich.console import Console
import json
from time import sleep
from rich.progress import Progress

console = Console()
with open("config.json") as f:
    config = json.load(f)
    console.log("loaded config")
    console.log(config)


def my_ip_address():
    """Cloudflare API code - example"""

    # This list is adjustable - plus some v6 enabled services are needed
    # url = 'http://myip.dnsomatic.com'
    # url = 'http://www.trackip.net/ip'
    # url = 'http://myexternalip.com/raw'
    url = "https://api.ipify.org"
    try:
        ip_address = requests.get(url).text
    except requests.exceptions.ConnectionError as e:
        exit("%s: failed - %s" % (url, e))
    if ip_address == "":
        exit("%s: failed" % (url))

    if ":" in ip_address:
        ip_address_type = "AAAA"
    else:
        ip_address_type = "A"

    return ip_address, ip_address_type


old_ip_address = None

cf = CloudFlare.CloudFlare(token=environ["TOKEN"])
zones = cf.zones.get()
zone = zones[0]

zone_id = zone["id"]
zone_name = zone["name"]
console.log("zone_id=%s zone_name=%s" % (zone_id, zone_name))


def do_dns_update(new_ip, ip_type):

    dns_records = cf.zones.dns_records.get(zone_id)
    with Progress(console=console) as progress:
        task_scan = progress.add_task("Scanning DNS...", total=len(dns_records))
        task_update = progress.add_task("Updating DNS...", total=len(config['records_to_update']))
        for dns_record in dns_records:
            progress.update(task_scan, advance=1)
            if dns_record['name'] in config['records_to_update'] and dns_record['type'] ==ip_type:
                progress.update(task_update, advance=1)
                console.log(f'Update {dns_record["name"]} from {dns_record["type"]} {dns_record["content"]} to {ip_type} {new_ip}')
                dns_record['content'] = new_ip
                cf.zones.dns_records.put(zone_id, dns_record['id'], data=dns_record)


def main():
    ip_address, ip_type = my_ip_address()
    while True:
        if ip_address != old_ip_address:
            old_ip_address = ip_address
            console.log(f"Detected new ip: {ip_type} {ip_address}")
            do_dns_update(ip_address, ip_type)
        sleep(60)

    # console.log(cf.zones.dns_records.get(zone_id))


if __name__ == "__main__":
    main()
