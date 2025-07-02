
# Important

Now recommend using Supervisord to manage the process of script.

Can also call by ppp ip-ip.d, see below.

## auto update dns record to cloudflare

design for ubuntu

track update log in dns_update.log

:::
Requires pppd to function properly
:::

:::
Please remember to remove the script before removed
:::

 install

create a file in `/etc/ppp/ip-up.d`

```bash
#!/bin/bash
cd /path/to/your/repo/

# Activate the virtual environment
source venv/bin/activate
# Run the Python script
python main.py "$PPP_LOCAL"
# Deactivate the virtual environment
deactivate
```

## files

.env

```plain
API_KEY=
API_EMAIL=
```

config.json

```json
{
    "zone_id": "",
    "debug": false,
    "records_to_update": [
        "one.example.com", 
        "two.example.com"
    ]
}
```
