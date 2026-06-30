"""
GeoAI-TKO · src/processing/train_lulc_rf.py
Trains an XGBoost land-cover classifier (v2) on the Sentinel-2 COG mosaic's
spectral indices + 3x3 texture (std) features, using ESA WorldCover as
ground truth. The WorldCover raster is warped onto the COG's exact pixel
grid on the fly via a WarpedVRT, so there's no separate alignment step and
no risk of the two rasters being off-grid relative to each other.

v2 vs the original RandomForest baseline (OOB 74.25%):
  - 7 extra texture features: std of each band over a 3x3 neighborhood
    (10m -> 30m window). Distinguishes regular urban texture (buildings,
    roads) from homogeneous bare soil/fields, which both have low NDVI
    and were getting confused on spectral-only features.
  - XGBoost instead of RandomForest.
  - sample_weight='balanced' instead of class_weight (XGBoost has no
    class_weight param; this is the equivalent for boosted trees).

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
from scipy.ndimage import uniform_filter
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.metrics import classification_report, confusion_matrix
from xgboost import XGBClassifier

COG_PATH = Path(r"D:\data\mosaics\2023_summer\s2_mosaic_cog.tif")
WORLDCOVER_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\data\reference\esa_worldcover_turkestan.tif")
MODEL_PATH = Path(r"D:\data\classifiers\lulc_classifier_v2.pkl")  # new file — old lulc_classifier.pkl is left untouched

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

# Order is critical — backend/main.py must build the inference feature
# vector in exactly this order. First 6 match the v1 model (so the spectral
# half is directly comparable); the 7 std_* features are the new texture
# block, in COG band order (B02,B03,B04,B05,B08,B8A,B11).
FEATURE_NAMES = [
    "ndvi", "ndre", "ndwi", "ndmi", "bsi", "b08",
    "std_b02", "std_b03", "std_b04", "std_b05", "std_b08", "std_b8a", "std_b11",
]


def block_texture_std(block: np.ndarray, nodata_mask: np.ndarray) -> np.ndarray:
    """3x3 std per band, nodata-aware (zero-weighted), vectorized over a whole block.

    block: (7, h, w) raw DN. nodata_mask: (h, w) bool, True = nodata pixel.
    Returns (7, h, w) std; 0 where a pixel has fewer than 3 valid neighbors —
    same fallback as the spec's per-pixel get_texture_features.

    Uses scipy's separable box filter (uniform_filter) instead of summing 9
    Python-level shifted-slice views per band: the hand-rolled version (sum()
    over 9 numpy slices x 2 accumulators x 7 bands) allocates dozens of large
    temporary arrays per block, which fragmented the allocator badly enough
    over ~100+ blocks that per-block time grew from ~16s to ~450s and rising.
    uniform_filter does the same 3x3-sum math (mode='constant' zero-pads at
    edges, matching the original padding behaviour) in C with flat, bounded
    memory use regardless of how many blocks have already been processed.
    """
    valid = (~nodata_mask).astype(np.float32)
    cnt = uniform_filter(valid, size=3, mode="constant", cval=0.0) * 9.0
    enough = cnt >= 3
    cnt3 = cnt[None, :, :]

    a = np.where(nodata_mask[None, :, :], 0.0, block).astype(np.float32)
    sum_a = uniform_filter(a, size=(1, 3, 3), mode="constant", cval=0.0) * 9.0
    sum_a2 = uniform_filter(a * a, size=(1, 3, 3), mode="constant", cval=0.0) * 9.0

    mean = np.divide(sum_a, cnt3, out=np.zeros_like(sum_a), where=cnt3 > 0)
    mean2 = np.divide(sum_a2, cnt3, out=np.zeros_like(sum_a2), where=cnt3 > 0)
    var = np.clip(mean2 - mean ** 2, 0, None)
    std = np.sqrt(var)
    std[:, ~enough] = 0.0
    return std.astype(np.float32)


def extract_samples():
    samples, labels = [], []
    total = 0
    with rasterio.open(COG_PATH) as cog:
        width, height = cog.width, cog.height

        # Warp ESA WorldCover onto the COG's exact grid ONCE, in memory, instead
        # of once per block. wc.read(1, window=...) on a WarpedVRT re-runs the
        # GDAL resampling/warp transform for that specific window on every call —
        # with ~120 blocks that's 120x the fixed warp overhead, which dominated
        # the original run (it was I/O-bound but on per-call warp setup, not raw
        # disk throughput — confirmed via CPU/IO counters during a 3h+ stalled
        # run). WorldCover itself is ~196MB; warped onto the full COG grid as
        # uint8 it's ~2.5GB, comfortably in memory, and a single warp pass is far
        # cheaper than 120 separate ones.
        print("Warping ESA WorldCover onto the COG grid (one-time, in memory)...")
        with rasterio.open(WORLDCOVER_PATH) as wc_src, WarpedVRT(
            wc_src, crs=cog.crs, transform=cog.transform,
            width=width, height=height, resampling=Resampling.nearest,
        ) as wc:
            wc_full = wc.read(1)  # (height, width) uint8
        print(f"  WorldCover warped: shape={wc_full.shape}, {wc_full.nbytes / 1e9:.2f} GB")

        for top in range(0, height, BLOCK_ROWS):
            h = min(BLOCK_ROWS, height - top)

            # Read 1 extra row of context above/below so the 3x3 texture
            # window is correct at block boundaries too (clipped at the
            # true raster edges, same as a per-pixel windowed read would be).
            ctx_top = max(0, top - 1)
            ctx_bottom = min(height, top + h + 1)
            ctx_block = cog.read(window=((ctx_top, ctx_bottom), (0, width))).astype(np.float32)
            ctx_nodata = np.all(ctx_block == 0, axis=0)
            ctx_std = block_texture_std(ctx_block, ctx_nodata)

            offset = top - ctx_top
            block = ctx_block[:, offset:offset + h, :]        # (7, h, width) raw DN
            std_block = ctx_std[:, offset:offset + h, :]      # (7, h, width) 3x3 std

            wc_block = wc_full[top:top + h, :]                # plain numpy slice, no I/O

            rs = np.arange(0, h, SAMPLE_STEP)
            cs = np.arange(0, width, SAMPLE_STEP)
            sub = block[:, rs][:, :, cs]              # (7, len(rs), len(cs))
            std_sub = std_block[:, rs][:, :, cs]      # (7, len(rs), len(cs))
            wc_sub = wc_block[rs][:, cs]

            b02, b03, b04, b05, b08, b8a, b11 = (sub[i].ravel() for i in range(7))
            s02, s03, s04, s05, s08, s8a, s11 = (std_sub[i].ravel() for i in range(7))
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

            feats = np.stack([
                ndvi, ndre, ndwi, ndmi, bsi, r08,
                s02[keep], s03[keep], s04[keep], s05[keep], s08[keep], s8a[keep], s11[keep],
            ], axis=1)
            samples.append(feats)
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
    print(f"Collected {len(X):,} samples, {X.shape[1]} features each")

    counts = {c: labels.count(c) for c in set(labels)}
    print(f"Class distribution: {counts}")

    le = LabelEncoder()
    y = le.fit_transform(labels)
    print(f"Classes: {list(le.classes_)}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42,
    )

    sample_weights = compute_sample_weight("balanced", y_train)

    print("\nTraining XGBoost...")
    clf = XGBClassifier(
        n_estimators=300, max_depth=6, learning_rate=0.1,
        subsample=0.8, colsample_bytree=0.8,
        tree_method="hist", n_jobs=-1, random_state=42,
        eval_metric="mlogloss",
    )
    clf.fit(X_train, y_train, sample_weight=sample_weights)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(clf, X_train, y_train, cv=cv, scoring="accuracy", n_jobs=-1)
    print(f"\nCV accuracy: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    y_pred = clf.predict(X_test)
    print("\nClassification report (held-out test set):")
    print(classification_report(y_test, y_pred, target_names=le.classes_))

    print("Confusion matrix (rows=true, cols=predicted):")
    print("Classes:", list(le.classes_))
    print(confusion_matrix(y_test, y_pred))

    print("\nFeature importance (top 10):")
    importances = clf.feature_importances_
    order = np.argsort(importances)[::-1][:10]
    for i in order:
        print(f"  {FEATURE_NAMES[i]}: {importances[i]:.4f}")

    model_bundle = {
        "model": clf,
        "label_encoder": le,
        "feature_names": FEATURE_NAMES,
        "n_classes": len(le.classes_),
        "classes": list(le.classes_),
    }
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model_bundle, f)
    print(f"\nModel saved: {MODEL_PATH}")


if __name__ == "__main__":
    main()
