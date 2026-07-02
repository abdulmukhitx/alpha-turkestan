import json
import pickle
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import shape, Point
from shapely.ops import unary_union
import random

MOSAICS_DIR = Path(r"D:\data\mosaics")
BOUNDARY_PATH = Path(r"C:\Users\USER\alpha-turkestan\frontend\public\turkestan_boundary.geojson")
CLASSIFIER_PATH = Path(r"D:\data\classifiers\lulc_classifier.pkl")

COG_2023 = MOSAICS_DIR / "2023_summer" / "s2_mosaic_cog_v2.tif"
COG_2025 = MOSAICS_DIR / "2025_summer" / "s2_mosaic_cog.tif"

with open(CLASSIFIER_PATH, "rb") as f:
    saved = pickle.load(f)
model = saved["model"]
label_encoder = saved["label_encoder"]

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


# Find points classified as agriculture/dense_vegetation in 2023
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

        ndvi25, ndre25, ndwi25, ndmi25, bsi25 = compute_feats(v25)
        feat25 = np.array([[ndvi25, ndre25, ndwi25, ndmi25, bsi25, v25[4]]], dtype=np.float32)
        pred25 = label_encoder.inverse_transform(model.predict(feat25))[0]

        found.append({
            "lon": lon, "lat": lat,
            "class_2023": pred23, "class_2025": pred25,
            "b08_2023": float(v23[4]), "b08_2025": float(v25[4]),
            "ndvi_2023": float(ndvi23), "ndvi_2025": float(ndvi25),
        })

print(f"Найдено {len(found)} точек (2023 класс agriculture/dense_vegetation), попыток: {attempts}\n")
print(f"{'class_2023':>16} {'class_2025':>18} {'B08_2023':>10} {'B08_2025':>10} {'NDVI_2023':>10} {'NDVI_2025':>10}")
for p in found:
    print(f"{p['class_2023']:>16} {p['class_2025']:>18} {p['b08_2023']:>10.4f} {p['b08_2025']:>10.4f} "
          f"{p['ndvi_2023']:>10.4f} {p['ndvi_2025']:>10.4f}")

ndvi23_arr = np.array([p["ndvi_2023"] for p in found])
ndvi25_arr = np.array([p["ndvi_2025"] for p in found])
b08_23_arr = np.array([p["b08_2023"] for p in found])
b08_25_arr = np.array([p["b08_2025"] for p in found])

print(f"\nСредний NDVI: 2023={ndvi23_arr.mean():.3f}  2025={ndvi25_arr.mean():.3f}")
print(f"Средний B08:  2023={b08_23_arr.mean():.4f}  2025={b08_25_arr.mean():.4f}")
n_became_bare = sum(1 for p in found if p["class_2025"] == "bare_soil")
print(f"\nИз {len(found)} точек (2023=agriculture/dense_veg) -> в 2025 стали bare_soil: {n_became_bare}")
