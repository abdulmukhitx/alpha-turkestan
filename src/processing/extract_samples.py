"""
GeoAI-TKO · src/processing/extract_samples.py
Extracts LULC training samples (5 spectral indices + B08 + 7 texture-std
features + ESA WorldCover class) from the Sentinel-2 COG mosaic and saves
them to a compressed .npz for train_xgb.py to consume.

Why per-point windowed reads instead of full-width block reads:
  An earlier version read full-width ~256-row blocks (~319MB each) and threw
  away ~98% of each block via SAMPLE_STEP downsampling after the read — i.e.
  it pulled the *entire* 34.5GB COG off disk just to keep 1/2500th of it.
  On this environment that triggered what looks like cloud block-storage I/O
  credit exhaustion: fast for the first few minutes (burst credits), then
  sustained throughput collapsed to a small fraction of the initial rate and
  stayed there (confirmed via isolated timing tests — the same texture-std
  code is flat and fast against synthetic in-memory data; it's specifically
  large *real* sequential reads that degrade). Reading a small 3x3 window
  directly at each sample point instead cuts total bytes read from ~34.5GB
  to a few hundred MB, which stays well under any credit-based throttling.

Usage:
  python src/processing/extract_samples.py [path/to/esa_worldcover.tif]
"""
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from pyproj import Transformer

COG_PATH = Path(r"D:\data\mosaics\2023_summer\s2_mosaic_cog.tif")
WORLDCOVER_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\data\reference\esa_worldcover_turkestan.tif")
OUT_PATH = Path(r"D:\data\samples\lulc_samples_v2.npz")

SAMPLE_STEP = 50          # ~500m spacing in COG pixels (10m/px)
CAP_PER_CLASS = 150_000   # per-class cap: balances classes and bounds runtime/output size
PRINT_EVERY = 5_000       # candidate points checked, not just hits

# ESA WorldCover class code -> simplified land-cover label for this region.
# Code 70 (Snow/Ice) is intentionally absent -> skipped, not relevant here.
ESA_TO_CLASS = {
    10: "dense_vegetation",   # Tree cover
    20: "sparse_vegetation",  # Shrubland
    30: "sparse_vegetation",  # Grassland
    40: "agriculture",        # Cropland
    50: "urban",              # Built-up
    60: "bare_soil",          # Bare / sparse vegetation
    80: "water",              # Permanent water bodies
    90: "water",              # Herbaceous wetland
    95: "dense_vegetation",   # Mangroves
    100: "bare_soil",         # Moss/lichen
}
TARGET_CLASSES = sorted(set(ESA_TO_CLASS.values()))

# Order is critical — train_xgb.py and backend/main.py must build feature
# vectors in exactly this order.
FEATURE_NAMES = [
    "ndvi", "ndre", "ndwi", "ndmi", "bsi", "b08",
    "std_b02", "std_b03", "std_b04", "std_b05", "std_b08", "std_b8a", "std_b11",
]


def read_point_window(src, row, col):
    """3x3 window around (row,col), clipped at the raster edges. Returns
    (7, h, w) raw DN (h,w in {2,3}) plus the center pixel's index within
    that window, or (None, None) if entirely out of bounds."""
    row_off, col_off = max(0, row - 1), max(0, col - 1)
    row_end, col_end = min(src.height, row + 2), min(src.width, col + 2)
    if row_end <= row_off or col_end <= col_off:
        return None, None
    window = Window(col_off, row_off, col_end - col_off, row_end - row_off)
    data = src.read(window=window)
    return data, (row - row_off, col - col_off)


def main():
    if not COG_PATH.exists():
        raise FileNotFoundError(f"COG not found: {COG_PATH}")
    if not WORLDCOVER_PATH.exists():
        raise FileNotFoundError(f"WorldCover file not found: {WORLDCOVER_PATH}")

    samples, labels = [], []
    class_counts = {c: 0 for c in TARGET_CLASSES}

    with rasterio.open(COG_PATH) as cog, rasterio.open(WORLDCOVER_PATH) as wc_src:
        width, height = cog.width, cog.height
        transformer = Transformer.from_crs(cog.crs, wc_src.crs, always_xy=True)

        rows = list(range(0, height, SAMPLE_STEP))
        cols = list(range(0, width, SAMPLE_STEP))
        total_points = len(rows) * len(cols)
        print(f"COG: {width}x{height}, sample grid {len(rows)}x{len(cols)} = {total_points:,} candidate points",
              flush=True)

        checked = 0
        capped_out = False
        for row in rows:
            for col in cols:
                checked += 1
                if checked % PRINT_EVERY == 0:
                    print(f"Checked {checked:,}/{total_points:,} points, samples collected: {len(samples):,}",
                          flush=True)

                win, center_idx = read_point_window(cog, row, col)
                if win is None:
                    continue
                win = win.astype(np.float32)
                nodata = np.all(win == 0, axis=0)
                cy, cx = center_idx
                if nodata[cy, cx]:
                    continue

                center = win[:, cy, cx]
                if not np.all(center > 0):
                    continue

                b02, b03, b04, b05, b08, b8a, b11 = (center / 10000.0)
                eps = 1e-10
                ndvi = (b08 - b04) / (b08 + b04 + eps)
                ndre = (b08 - b05) / (b08 + b05 + eps)
                ndwi = (b03 - b08) / (b03 + b08 + eps)
                ndmi = (b8a - b11) / (b8a + b11 + eps)
                bsi = ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02) + eps)

                valid_px = ~nodata
                if valid_px.sum() < 3:
                    stds = np.zeros(7, dtype=np.float32)
                else:
                    stds = win[:, valid_px].std(axis=1).astype(np.float32)

                x, y = cog.xy(row, col)
                wx, wy = transformer.transform(x, y)
                wc_val = next(wc_src.sample([(wx, wy)]))[0]
                cls = ESA_TO_CLASS.get(int(wc_val))
                if cls is None or class_counts[cls] >= CAP_PER_CLASS:
                    continue
                class_counts[cls] += 1

                feat = np.array([ndvi, ndre, ndwi, ndmi, bsi, b08, *stds], dtype=np.float32)
                samples.append(feat)
                labels.append(cls)

            if all(v >= CAP_PER_CLASS for v in class_counts.values()):
                print(f"All classes capped at {CAP_PER_CLASS:,} — stopping early "
                      f"({checked:,}/{total_points:,} points checked)", flush=True)
                capped_out = True
                break

        if not capped_out:
            print(f"Scanned full grid: {checked:,}/{total_points:,} points checked", flush=True)

    if not samples:
        raise RuntimeError("No training samples extracted — check COG/WorldCover overlap and paths.")

    X = np.stack(samples, axis=0)
    y = np.array(labels)
    print(f"\nTotal samples: {len(X):,}, features: {X.shape[1]}")
    print("Class distribution:", {c: int((y == c).sum()) for c in TARGET_CLASSES})

    np.savez_compressed(OUT_PATH, X=X, y=y)
    print(f"Saved: {OUT_PATH}")


if __name__ == "__main__":
    main()
