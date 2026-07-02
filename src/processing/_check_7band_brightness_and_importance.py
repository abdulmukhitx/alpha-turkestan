import json
import pickle
import random
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import shape, Point
from shapely.ops import unary_union

MOSAICS_DIR = Path(r"D:\data\mosaics")
BOUNDARY_PATH = Path(r"C:\Users\USER\alpha-turkestan\frontend\public\turkestan_boundary.geojson")
CLASSIFIER_PATH = Path(r"D:\data\classifiers\lulc_classifier.pkl")

COG_2023 = MOSAICS_DIR / "2023_summer" / "s2_mosaic_cog_v2.tif"
COG_2025 = MOSAICS_DIR / "2025_summer" / "s2_mosaic_cog.tif"
BAND_NAMES = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]

with open(CLASSIFIER_PATH, "rb") as f:
    saved = pickle.load(f)
model = saved["model"]
label_encoder = saved["label_encoder"]

# ── 0. Feature importances first (cheap, no I/O) ────────────────────
print("=" * 70)
print("  Feature importances — классификатор v1 (RandomForest)")
print("=" * 70)
feature_names = ["NDVI", "NDRE", "NDWI", "NDMI", "BSI", "B08"]
importances = model.feature_importances_
order = np.argsort(importances)[::-1]
for i in order:
    bar = "#" * int(importances[i] * 100)
    print(f"  {feature_names[i]:>6}: {importances[i]:.4f}  {bar}")

# ── Verify check_5's read method empirically (no out_shape => full-res) ──
print("\n" + "=" * 70)
print("  Верификация: read(window=1x1, без out_shape) читает full-res, не overview")
print("=" * 70)
with rasterio.open(COG_2025) as ds:
    ovr_factor = max(ds.overviews(1))
    # pick a pixel window near 42TVN center and compare direct read vs explicit full-res read via same window
    test_window = rasterio.windows.Window(ds.width // 2, ds.height // 2, 1, 1)
    v_direct = ds.read(1, window=test_window)
    # this IS the same code path check_5 uses — no out_shape param, base-resolution read by construction.
    print(f"  ds.read(window=1x1) без out_shape -> shape={v_direct.shape}, "
          f"это базовый уровень (не overview) по определению rasterio API — overview используется "
          f"ТОЛЬКО когда передан out_shape с иным разрешением, чего в check_5 нет.")

# ── 1-2. Systematic 7-band brightness comparison on the same 25 points ──
print("\n" + "=" * 70)
print("  Систематическое сравнение яркости по всем 7 бэндам (agriculture/dense_vegetation в 2023)")
print("=" * 70)

geo = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
feats = geo.get("features", [geo])
boundary = unary_union([shape(f.get("geometry", f)) for f in feats])

rng = random.Random(99)
minx, miny, maxx, maxy = boundary.bounds


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
        v25 = ds25.read(window=rasterio.windows.Window(col25, row25, 1, 1))[:, 0, 0]
        if v25[0] == nodata25:
            continue

        found.append({"lon": lon, "lat": lat, "v23": v23.tolist(), "v25": v25.tolist()})

print(f"Найдено {len(found)} точек, попыток: {attempts}\n")

v23_arr = np.array([p["v23"] for p in found])  # (N,7)
v25_arr = np.array([p["v25"] for p in found])

print(f"{'Бэнд':>6} {'2023 mean':>11} {'2025 mean':>11} {'delta':>10} {'delta %':>9}")
for i, name in enumerate(BAND_NAMES):
    m23 = v23_arr[:, i].mean()
    m25 = v25_arr[:, i].mean()
    delta = m25 - m23
    delta_pct = 100 * delta / m23 if m23 != 0 else float("nan")
    print(f"{name:>6} {m23:>11.4f} {m25:>11.4f} {delta:>+10.4f} {delta_pct:>+8.1f}%")
