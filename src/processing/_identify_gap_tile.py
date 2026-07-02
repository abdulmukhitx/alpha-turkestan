import json
from pathlib import Path
import rasterio
from rasterio.warp import transform_bounds
from shapely.geometry import box, Point

MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")
manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

targets = [("region_257", 68.6699, 42.858), ("region_220", 69.4872, 43.7466)]

for name, lon, lat in targets:
    pt = Point(lon, lat)
    print(f"{name}: ({lon},{lat})")
    matches = []
    for tile, info in manifest["tiles"].items():
        with rasterio.open(info["bands"]["B02"]) as ds:
            bounds_4326 = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
            if box(*bounds_4326).contains(pt):
                matches.append(tile)
    print(f"  тайл(ы): {matches}")
