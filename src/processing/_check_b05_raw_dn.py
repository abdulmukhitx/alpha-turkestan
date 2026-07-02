import json
import pickle
import random
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
from pyproj import Transformer
from shapely.geometry import shape, Point, box
from shapely.ops import unary_union

MOSAICS_DIR = Path(r"D:\data\mosaics")
BOUNDARY_PATH = Path(r"C:\Users\USER\alpha-turkestan\frontend\public\turkestan_boundary.geojson")
CLASSIFIER_PATH = Path(r"D:\data\classifiers\lulc_classifier.pkl")
MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")

COG_2023 = MOSAICS_DIR / "2023_summer" / "s2_mosaic_cog_v2.tif"
COG_2025 = MOSAICS_DIR / "2025_summer" / "s2_mosaic_cog.tif"

with open(CLASSIFIER_PATH, "rb") as f:
    saved = pickle.load(f)
model = saved["model"]
label_encoder = saved["label_encoder"]

manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

geo = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
feats = geo.get("features", [geo])
boundary = unary_union([shape(f.get("geometry", f)) for f in feats])

# build tile bbox lookup (4326) once
tile_bboxes = {}
for tile, info in manifest["tiles"].items():
    with rasterio.open(info["bands"]["B02"]) as ds:
        b = rasterio.warp.transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
        tile_bboxes[tile] = box(*b)


def compute_feats(vals):
    b02, b03, b04, b05, b08, b8a, b11 = vals
    eps = 1e-10
    ndvi = (b08 - b04) / (b08 + b04 + eps)
    ndre = (b08 - b05) / (b08 + b05 + eps)
    ndwi = (b03 - b08) / (b03 + b08 + eps)
    ndmi = (b8a - b11) / (b8a + b11 + eps)
    bsi = ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02) + eps)
    return ndvi, ndre, ndwi, ndmi, bsi


target_classes = {"agriculture", "dense_vegetation"}
rng = random.Random(99)
minx, miny, maxx, maxy = boundary.bounds
found = []
attempts = 0

with rasterio.open(COG_2023) as ds23, rasterio.open(COG_2025) as ds25:
    t23 = Transformer.from_crs("EPSG:4326", ds23.crs, always_xy=True)
    t25 = Transformer.from_crs("EPSG:4326", ds25.crs, always_xy=True)
    nodata23 = ds23.nodata
    nodata25 = ds25.nodata

    while len(found) < 25 and attempts < 20000:
        attempts += 1
        lon = rng.uniform(minx, maxx)
        lat = rng.uniform(miny, maxy)
        if not boundary.contains(Point(lon, lat)):
            continue

        x23, y23 = t23.transform(lon, lat)
        row23, col23 = ds23.index(x23, y23)
        if not (0 <= row23 < ds23.height and 0 <= col23 < ds23.width):
            continue
        v23 = ds23.read(window=rasterio.windows.Window(col23, row23, 1, 1))[:, 0, 0]
        if v23[0] == nodata23:
            continue

        ndvi23, ndre23, ndwi23, ndmi23, bsi23 = compute_feats(v23)
        feat23 = np.array([[ndvi23, ndre23, ndwi23, ndmi23, bsi23, v23[4]]], dtype=np.float32)
        pred23 = label_encoder.inverse_transform(model.predict(feat23))[0]
        if pred23 not in target_classes:
            continue

        x25, y25 = t25.transform(lon, lat)
        row25, col25 = ds25.index(x25, y25)
        if not (0 <= row25 < ds25.height and 0 <= col25 < ds25.width):
            continue
        v25_mosaic = ds25.read(window=rasterio.windows.Window(col25, row25, 1, 1))[:, 0, 0]
        if v25_mosaic[0] == nodata25:
            continue

        # which tile does this point fall in? (may be covered by >1 tile bbox at edges)
        candidate_tiles = [t for t, bb in tile_bboxes.items() if bb.contains(Point(lon, lat))]
        if not candidate_tiles:
            continue

        found.append({
            "lon": lon, "lat": lat, "mosaic_b05_reflectance": float(v25_mosaic[3]),
            "candidate_tiles": candidate_tiles,
        })

print(f"Найдено {len(found)} точек, попыток: {attempts}\n")

# For each point, read RAW DN B05 directly from the primary tile's own JP2
# (nearest pixel to that lon/lat), independent of the mosaic's compositing.
print(f"{'lon':>10} {'lat':>10} {'tile':>8} {'raw_DN_B05':>11} {'refl(-1000)':>12} {'mosaic_refl':>12}")
raw_vals = []
for p in found:
    tile = p["candidate_tiles"][0]
    b05_path = manifest["tiles"][tile]["bands"]["B05"]
    with rasterio.open(b05_path) as src:
        tr = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        x, y = tr.transform(p["lon"], p["lat"])
        row, col = src.index(x, y)
        if not (0 <= row < src.height and 0 <= col < src.width):
            continue
        dn = src.read(1, window=rasterio.windows.Window(col, row, 1, 1))[0, 0]
    if dn == 0:
        continue
    refl_m1000 = np.clip((float(dn) - 1000) / 10000.0, 0, 1)
    raw_vals.append({"tile": tile, "dn": int(dn), "refl_m1000": refl_m1000, "mosaic_refl": p["mosaic_b05_reflectance"]})
    print(f"{p['lon']:>10.4f} {p['lat']:>10.4f} {tile:>8} {int(dn):>11} {refl_m1000:>12.4f} {p['mosaic_b05_reflectance']:>12.4f}")

dn_arr = np.array([r["dn"] for r in raw_vals])
refl_arr = np.array([r["refl_m1000"] for r in raw_vals])
mosaic_arr = np.array([r["mosaic_refl"] for r in raw_vals])
print(f"\nСредний raw DN B05 (2025, source JP2): {dn_arr.mean():.1f}")
print(f"Средний reflectance после -1000/10000: {refl_arr.mean():.4f}")
print(f"Средний reflectance в итоговом mosaic (для сверки): {mosaic_arr.mean():.4f}")
print(f"Совпадают ли source-based и mosaic-based reflectance (в пределах compositing-вариаций)? "
      f"{'похоже да' if abs(refl_arr.mean()-mosaic_arr.mean()) < 0.02 else 'РАСХОДЯТСЯ -- нужно копать в compositing'}")

# sanity: what raw DN would we EXPECT for healthy red-edge vegetation reflectance (~0.3-0.4)?
expected_dn_for_03_035 = (np.array([0.30, 0.35]) * 10000) + 1000
print(f"\nОжидаемый raw DN для reflectance 0.30-0.35 (offset=-1000): {expected_dn_for_03_035}")
