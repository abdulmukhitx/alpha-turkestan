"""
GeoAI-TKO: Sentinel-2 Ingestion Pipeline — Day 1 MVP
=====================================================
Turkestan region, Kazakhstan
bbox: [67.5, 40.8, 71.5, 44.0]
Data: Sentinel-2 L2A (COPERNICUS/S2_SR_HARMONIZED)
Bands: B2 (blue), B3 (green), B4 (red), B8 (NIR)
Period: 2023-06-01 to 2023-09-30 (summer growing season)
Output: EPSG:32642, 10m resolution, GeoTIFF (via GEE Asset)
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

# ── CONFIG ───────────────────────────────────────────────────────
# Turkestan region bbox (WGS84)
TURKESTAN_BBOX = [67.5, 40.8, 71.5, 44.0]  # [min_lon, min_lat, max_lon, max_lat]

# Sentinel-2 parameters
START_DATE = "2023-06-01"
END_DATE = "2023-09-30"
CLOUD_COVER_MAX = 30  # percent
BANDS = ["B2", "B3", "B4", "B8"]  # Blue, Green, Red, NIR
TARGET_EPSG = 32642  # UTM zone 42N (covers Turkestan 66-72°E, bbox 67.5-71.5°E)
SCALE_M = 10  # meters per pixel

# Output
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # ingestion → src → project root
RAW_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_FILE = RAW_DIR / "sentinel2_day1.tif"

# ── GEE INIT ─────────────────────────────────────────────────────
import ee

# GCP Project ID (set via env var or override here)
GEE_PROJECT = os.environ.get("GEE_PROJECT", "tensile-oarlock-465814-m5")


def init_gee():
    """Initialize GEE with optional service account."""
    try:
        ee.Initialize(project=GEE_PROJECT)
        print(f"[GEE] Initialized. Project: {GEE_PROJECT}")
    except ee.EEException:
        print("[GEE] Not authenticated. Run: earthengine authenticate")
        print("[GEE] Attempting interactive auth...")
        try:
            ee.Authenticate()
            ee.Initialize(project=GEE_PROJECT)
            print(f"[GEE] Authenticated. Project: {GEE_PROJECT}")
        except Exception as e:
            print(f"[GEE] Auth failed: {e}")
            print("[GEE] Please manually run: earthengine authenticate")
            sys.exit(1)


# ── BBOX TO GEE ──────────────────────────────────────────────────
def bbox_to_ee_geometry(bbox):
    """Convert [min_lon, min_lat, max_lon, max_lat] to ee.Geometry."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])


# ── SENTINEL-2 COLLECTION ────────────────────────────────────────
def get_s2_collection(region, start_date, end_date, cloud_max):
    """Build filtered Sentinel-2 median composite."""
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_max))
        .select(BANDS)
    )
    return s2


# ── VISUALIZATION ────────────────────────────────────────────────
def add_ndvi(image):
    """Add NDVI band to image, cast to Float32 for export."""
    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI").toFloat()
    return image.addBands(ndvi)


# ── EXPORT TO GEE ASSET ──────────────────────────────────────────
def export_to_asset(image, region, asset_id=None):
    """Export image to GEE Asset (bypasses Drive 22GB limit)."""
    if asset_id is None:
        asset_id = f"projects/{GEE_PROJECT}/assets/sentinel2_tko_day1"
    task = ee.batch.Export.image.toAsset(
        image=image,
        description="sentinel2_tko_day1",
        assetId=asset_id,
        region=region,
        scale=SCALE_M,
        crs=f"EPSG:{TARGET_EPSG}",
        maxPixels=5e9,
    )
    task.start()
    return task


# ── MAIN ─────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  GeoAI-TKO: Sentinel-2 Ingestion — Day 1 MVP")
    print(f"  Started: {datetime.now().isoformat()}")
    print("=" * 60)

    # Ensure output dir
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n[FS] Output dir: {RAW_DIR}")

    # 1. Initialize GEE
    print("\n── Step 1: GEE Init ──")
    init_gee()

    # 2. Define region
    print("\n── Step 2: Define AOI ──")
    region = bbox_to_ee_geometry(TURKESTAN_BBOX)
    print(f"  bbox (WGS84): {TURKESTAN_BBOX}")
    print(f"  area (km²): {region.area().getInfo():.0f}")
    print(f"  target CRS: EPSG:{TARGET_EPSG}")

    # 3. Build collection
    print("\n── Step 3: Filter Sentinel-2 Collection ──")
    collection = get_s2_collection(region, START_DATE, END_DATE, CLOUD_COVER_MAX)
    count = collection.size().getInfo()
    print(f"  date range: {START_DATE} → {END_DATE}")
    print(f"  cloud cover < {CLOUD_COVER_MAX}%")
    print(f"  images found: {count}")

    if count == 0:
        print("[ERROR] No images found. Try relaxing cloud filter or date range.")
        sys.exit(1)

    # 4. Median composite (NO NDVI — avoid Float64/Float32 conflicts)
    print("\n── Step 4: Median Composite ──")
    median = collection.median().clip(region).toFloat()
    all_bands = BANDS
    print(f"  bands: {all_bands}")
    print(f"  scale: {SCALE_M}m")

    # 5. Export to GEE Asset (bypasses Drive 22GB limit)
    print("\n── Step 5: Export to GEE Asset ──")
    asset_id = f"projects/{GEE_PROJECT}/assets/sentinel2_tko_day1"
    print(f"  Asset ID: {asset_id}")
    print(f"  CRS: EPSG:{TARGET_EPSG}")
    print(f"  Resolution: {SCALE_M}m")
    print(f"  maxPixels: 5e9")
    print("  Starting export...")
    task = export_to_asset(median, region, asset_id)
    print(f"  Task ID: {task.id}")
    print(f"  Task status: {task.status()['state']}")
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║  TASK SUBMITTED TO GEE (Asset export)        ║")
    print("  ║  Monitor at:                                 ║")
    print("  ║  https://code.earthengine.google.com/tasks    ║")
    print(f"  ║  Asset: {asset_id}           ║")
    print("  ╚══════════════════════════════════════════════╝")

    # Save metadata
    metadata = {
        "project": "GeoAI-TKO",
        "date": datetime.now().isoformat(),
        "bbox": TURKESTAN_BBOX,
        "period": {"start": START_DATE, "end": END_DATE},
        "cloud_cover_max": CLOUD_COVER_MAX,
        "bands": all_bands,
        "crs": f"EPSG:{TARGET_EPSG}",
        "scale_m": SCALE_M,
        "images_in_collection": count,
        "gee_task_id": task.id,
        "output_file": str(OUTPUT_FILE),
    }
    meta_path = RAW_DIR / "sentinel2_day1_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"\n[FS] Metadata saved: {meta_path}")

    print("\n[DONE] Sentinel-2 export task submitted.")
    return task


if __name__ == "__main__":
    main()
