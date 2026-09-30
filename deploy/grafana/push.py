"""Upload uxie-dashboard.json to Grafana. Token: ~/.uxie/grafana_token (service account, Editor).

usage: python deploy/grafana/push.py https://<stack>.grafana.net
"""

import json
import sys
import urllib.request
from pathlib import Path

grafana = sys.argv[1].rstrip("/")
token = (Path.home() / ".uxie" / "grafana_token").read_text().strip()
dash = json.loads(Path(__file__).with_name("uxie-dashboard.json").read_text())
body = json.dumps({"dashboard": dash, "overwrite": True}).encode()
req = urllib.request.Request(
    f"{grafana}/api/dashboards/db",
    body,
    {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
)
print(grafana + json.load(urllib.request.urlopen(req))["url"])
