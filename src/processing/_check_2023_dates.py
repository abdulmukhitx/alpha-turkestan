import json
from pathlib import Path
from collections import Counter
import requests

REPORT_PATH = Path(r"D:\data\availability_report.json")
STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
COLLECTION = "sentinel-2-l2a"
CLOUD_COVER_MAX = 40

report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
master_tiles = set(report["master_tiles"])
aoi = report["aoi"]

body = {
    "collections": [COLLECTION],
    "intersects": aoi,
    "datetime": "2023-06-01T00:00:00Z/2023-08-31T23:59:59Z",
    "query": {"eo:cloud_cover": {"lt": CLOUD_COVER_MAX}},
    "limit": 200,
}
feats = []
url = STAC_URL
while True:
    r = requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    d = r.json()
    feats.extend(d.get("features", []))
    nxt = next((l for l in d.get("links", []) if l.get("rel") == "next"), None)
    if not nxt:
        break
    url = nxt["href"]
    body = nxt.get("body", body)

best = {}
for f in feats:
    p = f["properties"]
    tile = (p.get("grid:code") or "").replace("MGRS-", "")
    if tile not in master_tiles:
        continue
    cc = p.get("eo:cloud_cover")
    if cc is None:
        continue
    cur = best.get(tile)
    if cur is None or cc < cur[0]:
        best[tile] = (cc, p.get("datetime", "")[:10])

dates = [v[1] for v in best.values()]
c = Counter(sorted(dates))
for date, n in sorted(c.items()):
    print(date, n)
print()
months = Counter(d[:7] for d in dates)
print("Month distribution:")
for m, n in sorted(months.items()):
    print(m, n)
