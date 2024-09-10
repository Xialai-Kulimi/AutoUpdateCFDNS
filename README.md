
# auto update dns record to cloudflare

design for ubuntu

:::
Requires pppd to function properly
:::

:::
Please remember to uninstall the script before removed
:::

# install

create a file in `/etc/ppp/ip-up.d`

```bash
#!/bin/bash

# Activate the virtual environment
source /path/to/your/venv/bin/activate
# Run the Python script
python /path/to/your/main.py "$PPP_LOCAL"
# Deactivate the virtual environment
deactivate
```

# files

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
