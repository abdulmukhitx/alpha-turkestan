import json
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
from PIL import Image
from scipy import ndimage
from shapely.geometry import shape, Point, box
from shapely.ops import unary_union

COG_PATH = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
PREVIEW_PATH = Path(r"D:\data\mosaics\2025_summer\preview.png")
BOUNDARY_PATH = Path(r"C:\Users\USER\alpha-turkestan\frontend\public\turkestan_boundary.geojson")
MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")

with rasterio.open(COG_PATH) as ds:
    full_transform = ds.transform
    full_width, full_height = ds.width, ds.height
    crs = ds.crs

img = np.array(Image.open(PREVIEW_PATH).convert("RGB"))
prev_h, prev_w = img.shape[:2]
scale_x = full_width / prev_w
scale_y = full_height / prev_h

black_mask = np.all(img < 10, axis=2)

# 8-connectivity (include diagonals) so a rectangle rendered with slight
# anti-aliasing/gaps at overview resolution doesn't fragment into many
# separate 4-connected blobs that each fall under the size filter.
structure = np.ones((3, 3), dtype=int)
labeled, n_regions = ndimage.label(black_mask, structure=structure)
print(f"Найдено {n_regions} чёрных областей (8-связность)")

geo = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
feats = geo.get("features", [geo])
boundary = unary_union([shape(f.get("geometry", f)) for f in feats])

manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
tile_bboxes_4326 = {}
for tile, info in manifest["tiles"].items():
    with rasterio.open(info["bands"]["B02"]) as ds:
        b = rasterio.warp.transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
        tile_bboxes_4326[tile] = box(*b)

results = []
for region_id in range(1, n_regions + 1):
    ys, xs = np.where(labeled == region_id)
    size_px = len(ys)
    if size_px < 5:  # only filter true single-pixel noise
        continue
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    cy, cx = ys.mean(), xs.mean()

    full_col = cx * scale_x
    full_row = cy * scale_y
    map_x, map_y = rasterio.transform.xy(full_transform, full_row, full_col)
    lons, lats = rasterio.warp.transform(crs, "EPSG:4326", [map_x], [map_y])
    lon, lat = lons[0], lats[0]
    pt = Point(lon, lat)
    inside = boundary.contains(pt)

    tiles_here = [t for t, bb in tile_bboxes_4326.items() if bb.contains(pt)]

    results.append({
        "region_id": region_id, "size_px": size_px,
        "preview_bbox_px": (int(x0), int(y0), int(x1), int(y1)),
        "lon": round(lon, 4), "lat": round(lat, 4),
        "inside_boundary": inside, "tiles": tiles_here,
    })

results.sort(key=lambda r: -r["size_px"])
print(f"\n{'#':>4} {'size_px':>8} {'preview_bbox(x0,y0,x1,y1)':>28} {'lon':>9} {'lat':>9} {'внутри?':>8} {'tile':>10}")
for r in results[:40]:
    print(f"{r['region_id']:>4} {r['size_px']:>8} {str(r['preview_bbox_px']):>28} {r['lon']:>9} {r['lat']:>9} "
          f"{'ДА' if r['inside_boundary'] else 'нет':>8} {str(r['tiles']):>10}")

inside_results = [r for r in results if r["inside_boundary"]]
print(f"\nВсего областей (size>=5px): {len(results)}")
print(f"Внутри официальной границы: {len(inside_results)}")
for r in inside_results:
    print(f"  {r['tiles']}: size={r['size_px']}px, bbox_preview={r['preview_bbox_px']}, ({r['lon']},{r['lat']})")

Path(r"D:\data\black_holes_v2.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
