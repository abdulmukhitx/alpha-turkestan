import json
import time
import requests

# Direct item-level fetch via the public STAC API (no search, no auth needed
# for reading item metadata) — lighter than a collection search, less likely
# to hit the rate limit that blocked the search-based attempt.
PC_STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"

time.sleep(10)  # back off from the rate limit hit moments ago

body = {
    "collections": ["sentinel-2-l2a"],
    "bbox": [65.9, 40.5, 71.0, 46.0],
    "datetime": "2023-07-01/2023-07-31",
    "query": {"eo:cloud_cover": {"lt": 20}},
    "limit": 1,
}
r = requests.post(PC_STAC_SEARCH, json=body, timeout=30)
print("status:", r.status_code)
if r.status_code != 200:
    print(r.text[:500])
else:
    feats = r.json().get("features", [])
    if not feats:
        print("no features found")
    else:
        item = feats[0]
        print("item id:", item["id"])
        b05 = item["assets"].get("B05")
        print(json.dumps(b05, indent=2, ensure_ascii=False))
