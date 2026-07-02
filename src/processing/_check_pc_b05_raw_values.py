import json
import pickle
import random
import time
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import shape, Point
from shapely.ops import unary_union
import requests

MOSAICS_DIR = Path(r"D:\data\mosaics")
BOUNDARY_PATH = Path(r"C:\Users\USER\alpha-turkestan\frontend\public\turkestan_boundary.geojson")
CLASSIFIER_PATH = Path(r"D:\data\classifiers\lulc_classifier.pkl")
COG_2023 = MOSAICS_DIR / "2023_summer" / "s2_mosaic_cog_v2.tif"

PC_STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
PC_SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"

with open(CLASSIFIER_PATH, "rb") as f:
    saved = pickle.load(f)
model = saved["model"]
label_encoder = saved["label_encoder"]

geo = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
feats = geo.get("features", [geo])
boundary = unary_union([shape(f.get("geometry", f)) for f in feats])

rng = random.Random(99)  # SAME seed as _check_7band_brightness_and_importance.py / _check_b05_raw_dn.py
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

with rasterio.open(COG_2023) as ds23:
    t23 = Transformer.from_crs("EPSG:4326", ds23.crs, always_xy=True)
    nodata23 = ds23.nodata
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
        found.append({"lon": lon, "lat": lat})

print(f"Точки восстановлены (тот же seed=99): {len(found)}, попыток: {attempts}\n")

results = []
for i, p in enumerate(found):
    lon, lat = p["lon"], p["lat"]
    body = {
        "collections": ["sentinel-2-l2a"],
        "intersects": {"type": "Point", "coordinates": [lon, lat]},
        "datetime": "2023-06-01/2023-08-31",
        "query": {"eo:cloud_cover": {"lt": 30}},
        "sortby": [{"field": "eo:cloud_cover", "direction": "asc"}],
        "limit": 1,
    }
    try:
        r = requests.post(PC_STAC_SEARCH, json=body, timeout=30)
        r.raise_for_status()
        feats_r = r.json().get("features", [])
        if not feats_r:
            print(f"[{i+1}/{len(found)}] ({lon:.4f},{lat:.4f}): нет продукта PC")
            continue
        item = feats_r[0]
        b05_asset = item["assets"]["B05"]
        href = b05_asset["href"]

        sign_r = requests.get(PC_SIGN_URL, params={"href": href}, timeout=30)
        sign_r.raise_for_status()
        signed_href = sign_r.json()["href"]

        with rasterio.open(signed_href) as src:
            tr = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            x, y = tr.transform(lon, lat)
            row, col = src.index(x, y)
            if not (0 <= row < src.height and 0 <= col < src.width):
                print(f"[{i+1}/{len(found)}] точка вне растра PC")
                continue
            dn = src.read(1, window=rasterio.windows.Window(col, row, 1, 1))[0, 0]

        results.append({"lon": lon, "lat": lat, "pc_item": item["id"], "dn_raw": int(dn)})
        print(f"[{i+1}/{len(found)}] ({lon:.4f},{lat:.4f}) item={item['id'][:30]} raw_DN_B05={int(dn)}")
        time.sleep(0.3)  # be gentle with PC's rate limit after the earlier hit
    except Exception as e:
        print(f"[{i+1}/{len(found)}] ОШИБКА: {e}")
        time.sleep(1)

if results:
    dn_arr = np.array([r["dn_raw"] for r in results])
    print(f"\nПолучено {len(results)}/{len(found)} точек из PC")
    print(f"Средний raw DN B05 (PC): {dn_arr.mean():.1f}")

    print("\n" + "=" * 70)
    print("  Гипотезы для reflectance-конвертации PC DN")
    print("=" * 70)
    # Hypothesis A: PC DN needs NO offset (already harmonized to old-baseline convention, offset=0)
    refl_a = dn_arr / 10000.0
    print(f"  A) reflectance = DN/10000 (offset=0):        mean={refl_a.mean():.4f}")
    # Hypothesis B: PC DN still needs the standard -1000 offset (in case PC preserved raw baseline>=4.0 values)
    refl_b = np.clip((dn_arr - 1000) / 10000.0, 0, 1)
    print(f"  B) reflectance = (DN-1000)/10000:            mean={refl_b.mean():.4f}")

    print(f"\n  Для сравнения: CDSE B05 raw DN (Step 1) mean=3214.7 -> reflectance(-1000)=0.2215")
    print(f"  Ожидаемый физический диапазон для здоровой растительности: 0.30-0.35")

    Path(r"D:\data\pc_b05_raw_check.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
else:
    print("\nНе удалось получить ни одной точки из PC.")
