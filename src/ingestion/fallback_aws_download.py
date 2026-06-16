"""
GeoAI-TKO: Download Sentinel-2 L2A from AWS Open Data (STAC-aware)
====================================================================
Uses Element84 STAC API to find scenes and grab band COGs directly.
Downloads B02,B03,B04,B08 for a single Sentinel-2 scene over Turkestan.
"""

import sys
import requests
from pathlib import Path
import rasterio
from rasterio.io import MemoryFile

TURKESTAN_BBOX = [67.5, 40.8, 71.5, 44.0]
OUTPUT_FILE = Path("data/raw/sentinel2_day1.tif")

STAC_SEARCH = "https://earth-search.aws.element84.com/v1/search"
STAC_ITEM_BASE = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items"

# Map our band names → STAC asset keys
BAND_MAP = {"B2": "blue", "B3": "green", "B4": "red", "B8": "nir"}


def find_best_scene():
    """Find clearest Sentinel-2 scene for Turkestan region."""
    params = {
        "collections": "sentinel-2-l2a",
        "bbox": f"{TURKESTAN_BBOX[0]},{TURKESTAN_BBOX[1]},{TURKESTAN_BBOX[2]},{TURKESTAN_BBOX[3]}",
        "datetime": "2023-06-01T00:00:00Z/2023-09-30T00:00:00Z",
        "limit": 20,
        "query": '{"eo:cloud_cover":{"lt":10}}',
    }
    resp = requests.get(STAC_SEARCH, params=params, timeout=30)
    data = resp.json()
    features = data.get("features", [])
    print(f"[STAC] Found {len(features)} scenes (cloud < 10%)")

    if not features:
        return None

    best = min(features, key=lambda f: f["properties"].get("eo:cloud_cover", 100))
    return best


def download_scene_bands(feature):
    """Download all 4 bands for a scene, combine into GeoTIFF."""
    scene_id = feature["id"]
    cloud = feature["properties"]["eo:cloud_cover"]
    date = feature["properties"]["datetime"]
    print(f"\nScene: {scene_id}")
    print(f"  Cloud: {cloud:.1f}%")
    print(f"  Date:  {date}")

    # Get full STAC item with all asset URLs
    item_url = f"{STAC_ITEM_BASE}/{scene_id}"
    item_resp = requests.get(item_url, timeout=30)
    item = item_resp.json()
    assets = item.get("assets", {})

    band_data = {}
    for our_band, stac_key in BAND_MAP.items():
        if stac_key not in assets:
            print(f"  [SKIP] {our_band} ({stac_key}) not in assets")
            continue
        url = assets[stac_key]["href"]
        print(f"  Downloading {our_band}: {url.split('/')[-1]}...", end=" ", flush=True)
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code == 200:
                print(f"OK ({len(resp.content)/1024/1024:.1f} MB)")
                band_data[our_band] = resp.content
            else:
                print(f"HTTP {resp.status_code}")
        except Exception as e:
            print(f"ERROR: {e}")

    if len(band_data) < 4:
        print(f"[WARN] Only {len(band_data)}/4 bands downloaded")
        return None

    # Write composite GeoTIFF
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with MemoryFile(band_data["B2"]) as mem:
        with mem.open() as src:
            profile = src.profile.copy()
            profile.update(count=4, dtype="float32")

    with rasterio.open(OUTPUT_FILE, "w", **profile) as dst:
        for idx, band_name in enumerate(["B2", "B3", "B4", "B8"], 1):
            with MemoryFile(band_data[band_name]) as mem:
                with mem.open() as src:
                    dst.write(src.read(1).astype("float32"), idx)
            dst.set_band_description(idx, band_name)
            print(f"  Wrote band {idx}: {band_name}")

    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"\n[OK] Saved: {OUTPUT_FILE} ({size_mb:.1f} MB)")
    return OUTPUT_FILE


if __name__ == "__main__":
    print("GeoAI-TKO: Sentinel-2 AWS Download")
    print("=" * 50)

    scene = find_best_scene()
    if not scene:
        print("[FAIL] No suitable scenes found")
        sys.exit(1)

    result = download_scene_bands(scene)
    if result:
        print(f"\n[DONE] {result}")
    else:
        print("\n[FAIL] Download incomplete")
        sys.exit(1)
