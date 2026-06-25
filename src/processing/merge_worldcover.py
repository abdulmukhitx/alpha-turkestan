"""
GeoAI-TKO · src/processing/merge_worldcover.py
Merges the 9 downloaded ESA WorldCover 10m v200 (2021) tiles into a single
cropped GeoTIFF covering the Turkestan Oblast AOI. This is the file
train_lulc_rf.py expects as its WORLDCOVER_PATH default.

Usage:
  python src/processing/merge_worldcover.py
"""
from pathlib import Path

import rasterio
from rasterio.merge import merge

RAW_DIR = Path(r"D:\data\worldcover_raw")
OUT_PATH = Path(r"D:\data\esa_worldcover_turkestan.tif")

TILES = [
    "N39E063", "N39E066", "N39E069",
    "N42E063", "N42E066", "N42E069",
    "N45E063", "N45E066", "N45E069",
]

# AOI bbox in EPSG:4326 (lon_min, lat_min, lon_max, lat_max)
AOI_BOUNDS = (65.94, 40.99, 70.66, 46.20)


def main():
    paths = [RAW_DIR / f"ESA_WorldCover_10m_2021_v200_{t}_Map.tif" for t in TILES]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing tiles: {missing}")

    srcs = [rasterio.open(p) for p in paths]
    try:
        mosaic, out_transform = merge(srcs, bounds=AOI_BOUNDS)
        meta = srcs[0].meta.copy()
    finally:
        for s in srcs:
            s.close()

    meta.update({
        "height": mosaic.shape[1],
        "width": mosaic.shape[2],
        "transform": out_transform,
        "compress": "deflate",
        "predictor": 2,
    })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(OUT_PATH, "w", **meta) as dst:
        dst.write(mosaic)

    print(f"Merged {len(paths)} tiles -> {OUT_PATH}")
    print(f"  shape: {mosaic.shape}, dtype: {mosaic.dtype}")
    print(f"  size: {OUT_PATH.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
