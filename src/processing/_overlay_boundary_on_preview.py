import json
from pathlib import Path

import rasterio
from rasterio.warp import transform_geom
from PIL import Image, ImageDraw
from shapely.geometry import shape

COG_PATH = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
PREVIEW_PATH = Path(r"D:\data\mosaics\2025_summer\preview.png")
BOUNDARY_PATH = Path(r"C:\Users\USER\alpha-turkestan\frontend\public\turkestan_boundary.geojson")
OUT_PATH = Path(r"D:\data\mosaics\2025_summer\preview_with_boundary.png")

with rasterio.open(COG_PATH) as ds:
    full_transform = ds.transform
    full_width, full_height = ds.width, ds.height
    crs = ds.crs

img = Image.open(PREVIEW_PATH).convert("RGB")
prev_w, prev_h = img.size
scale_x = prev_w / full_width
scale_y = prev_h / full_height

geo = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
feats = geo.get("features", [geo])

draw = ImageDraw.Draw(img)

for feat in feats:
    geom_4326 = feat.get("geometry", feat)
    geom_proj = transform_geom("EPSG:4326", crs, geom_4326)
    g = shape(geom_proj)
    polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
    for poly in polys:
        exterior = list(poly.exterior.coords)
        pixel_coords = []
        for x, y in exterior:
            row, col = rasterio.transform.rowcol(full_transform, x, y)
            px = col * scale_x
            py = row * scale_y
            pixel_coords.append((px, py))
        draw.line(pixel_coords, fill=(255, 0, 0), width=2)

img.save(OUT_PATH)
print(f"Сохранено: {OUT_PATH} ({img.width}x{img.height}px)")
