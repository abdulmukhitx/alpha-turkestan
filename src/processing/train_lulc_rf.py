"""
GeoAI-TKO · src/processing/train_lulc_rf.py
Trains a RandomForest land-cover classifier on the Sentinel-2 COG mosaic's
spectral indices, using ESA WorldCover as ground truth. The WorldCover
raster is warped onto the COG's exact pixel grid on the fly via a
WarpedVRT, so there's no separate alignment step and no risk of the two
rasters being off-grid relative to each other.

Usage:
  python src/processing/train_lulc_rf.py [path/to/esa_worldcover.tif]
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder

COG_PATH = Path(r"D:\data\s2_mosaic_cog.tif")
WORLDCOVER_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\data\esa_worldcover_turkestan.tif")
MODEL_PATH = Path(r"D:\data\lulc_classifier.pkl")

# Every 30th pixel on both axes (~300m spacing). The COG is 41812x61041px
# (~2.55B px) so sampling every pixel is neither necessary nor finishable.
SAMPLE_STEP = 50
BLOCK_ROWS = 512  # one read per ~tile-height instead of one per sample point

# ESA WorldCover class code -> simplified land-cover label for this region
ESA_TO_CLASS = {
    10: "dense_vegetation",   # Tree cover
    20: "sparse_vegetation",  # Shrubland
    30: "sparse_vegetation",  # Grassland
    40: "agriculture",        # Cropland
    50: "urban",              # Built-up
    60: "bare_soil",          # Bare / sparse vegetation
    80: "water",              # Permanent water bodies
    90: "sparse_vegetation",  # Herbaceous wetland
}
FEATURE_NAMES = ["NDVI", "NDRE", "NDWI", "NDMI", "BSI", "B08"]


def extract_samples():
    samples, labels = [], []
    total = 0
    with rasterio.open(COG_PATH) as cog:
        width, height = cog.width, cog.height
        with rasterio.open(WORLDCOVER_PATH) as wc_src, WarpedVRT(
            wc_src, crs=cog.crs, transform=cog.transform,
            width=width, height=height, resampling=Resampling.nearest,
        ) as wc:
            for top in range(0, height, BLOCK_ROWS):
                h = min(BLOCK_ROWS, height - top)
                window = ((top, top + h), (0, width))

                block = cog.read(window=window).astype(np.float32)  # (7, h, width)
                wc_block = wc.read(1, window=window)                 # (h, width)

                rs = np.arange(0, h, SAMPLE_STEP)
                cs = np.arange(0, width, SAMPLE_STEP)
                sub = block[:, rs][:, :, cs]       # (7, len(rs), len(cs))
                wc_sub = wc_block[rs][:, cs]       # (len(rs), len(cs))

                b02, b03, b04, b05, b08, b8a, b11 = (sub[i].ravel() for i in range(7))
                esa = wc_sub.ravel()

                valid = (b02 > 0) & (b03 > 0) & (b04 > 0) & (b05 > 0) & (b08 > 0) & (b8a > 0) & (b11 > 0)
                class_ok = np.isin(esa, list(ESA_TO_CLASS.keys()))
                keep = valid & class_ok
                if not keep.any():
                    continue

                r02, r03, r04 = b02[keep] / 10000.0, b03[keep] / 10000.0, b04[keep] / 10000.0
                r05, r08, r8a, r11 = b05[keep] / 10000.0, b08[keep] / 10000.0, b8a[keep] / 10000.0, b11[keep] / 10000.0
                eps = 1e-10

                ndvi = (r08 - r04) / (r08 + r04 + eps)
                ndre = (r08 - r05) / (r08 + r05 + eps)
                ndwi = (r03 - r08) / (r03 + r08 + eps)
                ndmi = (r8a - r11) / (r8a + r11 + eps)
                bsi = ((r11 + r04) - (r08 + r02)) / ((r11 + r04) + (r08 + r02) + eps)

                samples.append(np.stack([ndvi, ndre, ndwi, ndmi, bsi, r08], axis=1))
                labels.extend(ESA_TO_CLASS[int(c)] for c in esa[keep])

                total += int(keep.sum())
                print(f"  row {top}/{height} - {total:,} samples", end="\r")

    print()
    if not samples:
        raise RuntimeError("No training samples extracted - check COG/WorldCover overlap and paths.")
    return np.concatenate(samples, axis=0), labels


def main():
    if not COG_PATH.exists():
        raise FileNotFoundError(f"COG not found: {COG_PATH}")
    if not WORLDCOVER_PATH.exists():
        raise FileNotFoundError(f"WorldCover file not found: {WORLDCOVER_PATH}")

    print("Extracting training samples (COG x ESA WorldCover, grid-aligned on the fly)...")
    X, labels = extract_samples()
    print(f"Collected {len(X):,} samples")

    counts = {c: labels.count(c) for c in set(labels)}
    print(f"Class distribution: {counts}")

    le = LabelEncoder()
    y = le.fit_transform(labels)
    print(f"Classes: {list(le.classes_)}")

    print("\nTraining RandomForest...")
    clf = RandomForestClassifier(
        n_estimators=300, max_depth=25,
        min_samples_split=5,min_samples_leaf=5,
        class_weight="balanced", oob_score=True,
        random_state=42, n_jobs=-1,
    )
    clf.fit(X, y)

    print(f"Out-of-bag score: {clf.oob_score_:.4f}")
    for name, imp in zip(FEATURE_NAMES, clf.feature_importances_):
        print(f"  {name}: {imp:.4f}")

    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": clf, "label_encoder": le, "feature_names": FEATURE_NAMES}, f)
    print(f"\nModel saved: {MODEL_PATH}")


if __name__ == "__main__":
    main()
