import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from PIL import Image
from scipy import ndimage
from shapely.geometry import shape, Point
from shapely.ops import unary_union

COG_PATH = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
PREVIEW_PATH = Path(r"D:\data\mosaics\2025_summer\preview.png")
BOUNDARY_PATH = Path(r"C:\Users\USER\alpha-turkestan\frontend\public\turkestan_boundary.geojson")

with rasterio.open(COG_PATH) as ds:
    full_transform = ds.transform
    full_width, full_height = ds.width, ds.height
    crs = ds.crs

img = np.array(Image.open(PREVIEW_PATH).convert("RGB"))
prev_h, prev_w = img.shape[:2]
scale_x = full_width / prev_w
scale_y = full_height / prev_h

# black hole = near-zero RGB (nodata rendered as (0,0,0) since we zero it before stretch)
black_mask = np.all(img < 10, axis=2)

labeled, n_regions = ndimage.label(black_mask)
print(f"Найдено {n_regions} чёрных областей (nodata) на превью")

geo = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
feats = geo.get("features", [geo])
boundary = unary_union([shape(f.get("geometry", f)) for f in feats])

transformer_to_wgs84 = rasterio.warp.transform

results = []
for region_id in range(1, n_regions + 1):
    ys, xs = np.where(labeled == region_id)
    size_px = len(ys)
    if size_px < 20:  # skip tiny noise specks
        continue
    cy, cx = ys.mean(), xs.mean()
    # preview pixel -> full-res raster pixel -> map coords -> WGS84
    full_col = cx * scale_x
    full_row = cy * scale_y
    map_x, map_y = rasterio.transform.xy(full_transform, full_row, full_col)
    lons, lats = rasterio.warp.transform(crs, "EPSG:4326", [map_x], [map_y])
    lon, lat = lons[0], lats[0]
    inside = boundary.contains(Point(lon, lat))
    results.append({"region_id": region_id, "size_px": size_px, "centroid_preview_xy": (cx, cy),
                     "lon": round(lon, 4), "lat": round(lat, 4), "inside_boundary": inside})

results.sort(key=lambda r: -r["size_px"])
print(f"\n{'#':>4} {'size_px':>8} {'lon':>10} {'lat':>10} {'внутри границы?':>18}")
n_inside = 0
for r in results:
    marker = "  <-- ВНУТРИ, проблема!" if r["inside_boundary"] else ""
    if r["inside_boundary"]:
        n_inside += 1
    print(f"{r['region_id']:>4} {r['size_px']:>8} {r['lon']:>10} {r['lat']:>10} "
          f"{'ДА' if r['inside_boundary'] else 'нет (buffer-зона)':>18}{marker}")

print(f"\nИтого крупных чёрных областей: {len(results)}")
print(f"Внутри официальной границы: {n_inside}")
print(f"Вне границы (buffer-зона AOI, не проблема): {len(results) - n_inside}")
