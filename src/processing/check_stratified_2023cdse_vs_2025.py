"""
GeoAI-TKO · src/processing/check_stratified_2023cdse_vs_2025.py
=======================================================
ЭТАП 5, ключевая проверка — B05 (и все 7 бэндов) на ~25 точках/класс между
НОВЫМ 2023_summer_cdse и 2025_summer. Оба периода теперь из ОДНОГО источника
(CDSE) и оба уже физический float32 reflectance (nodata=-9999) — в отличие
от вчерашней проверки (check_offset_stratified.py), которая сравнивала
2023_summer (Planetary Computer) с offset=0 против 2025 CDSE с offset=-1000
как RAW DN. Здесь оба файла уже сконвертированы, поэтому ratio считается
напрямую по reflectance, без офсетов/масштабирования заново.

Ожидание: ratio должен быть близко к 1.0 (±5-15%, объяснимо сезонностью/
датой съёмки), НЕ структурный ~35% сдвиг, который был между PC и CDSE.

Метод отбора контрольных точек и стратификации по классу — тот же, что
вчера (check_offset_stratified.py): случайные блоки внутри общей области
пересечения растров, классификация по v1-классификатору на 2023-стороне
как источник истины лейбла.

Usage:
  python src/processing/check_stratified_2023cdse_vs_2025.py
"""
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import rowcol, xy
from rasterio.windows import Window

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

COG_2023 = Path(r"D:\data\mosaics\2023_summer_cdse\s2_mosaic_cog.tif")
COG_2025 = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
CLASSIFIER_PATH = Path(r"D:\data\classifiers\lulc_classifier.pkl")
OUT_PATH = Path(r"D:\data\mosaics\2023_summer_cdse\cross_period_check_vs_2025.json")

N_TARGET_PER_CLASS = 25
BLOCK = 512
CLASSES = ["water", "dense_vegetation", "agriculture", "sparse_vegetation", "bare_soil", "urban"]
BAND_NAMES = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]


def features_batch(arr):
    # arr shape (7, H, W): B02,B03,B04,B05,B08,B8A,B11 — already reflectance
    b02, b03, b04, b05, b08, b8a, b11 = arr.astype(np.float32)
    eps = 1e-6
    ndvi = (b08 - b04) / (b08 + b04 + eps)
    ndre = (b08 - b05) / (b08 + b05 + eps)
    ndwi = (b03 - b08) / (b03 + b08 + eps)
    ndmi = (b08 - b11) / (b08 + b11 + eps)
    bsi = ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02) + eps)
    return np.stack([ndvi, ndre, ndwi, ndmi, bsi, b08], axis=-1)  # (H,W,6)


def main():
    t0 = time.time()
    with open(CLASSIFIER_PATH, "rb") as f:
        clf_bundle = pickle.load(f)
    clf = clf_bundle["model"]
    label_encoder = clf_bundle["label_encoder"]
    label_map = {i: str(name) for i, name in enumerate(label_encoder.classes_)}
    print(f"Классификатор загружен: {type(clf).__name__}, label_map: {label_map}", flush=True)

    ds23 = rasterio.open(COG_2023)
    ds25 = rasterio.open(COG_2025)
    nodata23 = ds23.nodata if ds23.nodata is not None else -9999.0
    nodata25 = ds25.nodata if ds25.nodata is not None else -9999.0

    minx = max(ds23.bounds.left, ds25.bounds.left) + 20000
    maxx = min(ds23.bounds.right, ds25.bounds.right) - 20000
    miny = max(ds23.bounds.bottom, ds25.bounds.bottom) + 20000
    maxy = min(ds23.bounds.top, ds25.bounds.top) - 20000
    print(f"Общая область пересечения (с отступом 20км): x=[{minx:.0f},{maxx:.0f}] y=[{miny:.0f},{maxy:.0f}]")

    rng = np.random.default_rng(11)
    by_class = {c: [] for c in CLASSES}  # each item: dict band -> ratio, for one pixel
    blocks_read = 0
    max_blocks = 400

    while blocks_read < max_blocks and any(len(v) < N_TARGET_PER_CLASS for v in by_class.values()):
        blocks_read += 1
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        r23, c23 = rowcol(ds23.transform, x, y)
        if not (0 <= r23 < ds23.height - BLOCK and 0 <= c23 < ds23.width - BLOCK):
            continue

        block23 = ds23.read(window=Window(c23, r23, BLOCK, BLOCK))  # (7,H,W) float32 reflectance
        valid23 = np.all(block23 != nodata23, axis=0)
        if not valid23.any():
            continue

        left_x, top_y = xy(ds23.transform, r23, c23, offset="ul")
        r25, c25 = rowcol(ds25.transform, left_x, top_y)
        if not (0 <= r25 < ds25.height - BLOCK and 0 <= c25 < ds25.width - BLOCK):
            continue
        block25 = ds25.read(window=Window(c25, r25, BLOCK, BLOCK))
        valid25 = np.all(block25 != nodata25, axis=0)

        valid = valid23 & valid25
        n_valid = valid.sum()
        if n_valid == 0:
            continue

        feats = features_batch(block23)[valid]  # (N,6)
        preds = clf.predict(feats)

        v23 = block23[:, valid]  # (7,N) reflectance
        v25 = block25[:, valid]
        ratios_per_band = np.divide(v25, v23, out=np.full_like(v23, np.nan), where=v23 != 0)  # (7,N)

        for i in range(len(preds)):
            cname = label_map[int(preds[i])]
            if cname in by_class and len(by_class[cname]) < N_TARGET_PER_CLASS:
                by_class[cname].append({
                    "band_ratios": {BAND_NAMES[b]: float(ratios_per_band[b, i]) for b in range(7)},
                    "refl_2023": {BAND_NAMES[b]: float(v23[b, i]) for b in range(7)},
                    "refl_2025": {BAND_NAMES[b]: float(v25[b, i]) for b in range(7)},
                })

        elapsed = time.time() - t0
        counts = {c: len(v) for c, v in by_class.items()}
        print(f"  [блок {blocks_read}/{max_blocks}, {elapsed:.0f}s] найдено: {counts}", flush=True)

    ds23.close()
    ds25.close()

    print(f"\nБлоков прочитано: {blocks_read}, время: {time.time()-t0:.0f}s")

    per_class_band_stats = {}
    total_pts = 0
    print(f"\n{'Класс':>25} {'N':>4}  " + "  ".join(f"{b:>8}" for b in BAND_NAMES))
    for c in CLASSES:
        pts = by_class[c]
        total_pts += len(pts)
        if not pts:
            print(f"{c:>25} {'0':>4}  (нет точек)")
            per_class_band_stats[c] = None
            continue
        band_means = {}
        band_abs_2023 = {}
        band_abs_2025 = {}
        for b in BAND_NAMES:
            ratios = np.array([p["band_ratios"][b] for p in pts])
            ratios = ratios[~np.isnan(ratios)]
            band_means[b] = round(float(ratios.mean()), 3) if ratios.size else None
            band_abs_2023[b] = round(float(np.mean([p["refl_2023"][b] for p in pts])), 4)
            band_abs_2025[b] = round(float(np.mean([p["refl_2025"][b] for p in pts])), 4)
        per_class_band_stats[c] = {
            "n": len(pts), "mean_ratio_per_band": band_means,
            "mean_refl_2023": band_abs_2023, "mean_refl_2025": band_abs_2025,
        }
        print(f"    2023_cdse abs: " + "  ".join(f"{band_abs_2023[b]:>8.4f}" for b in BAND_NAMES))
        print(f"    2025      abs: " + "  ".join(f"{band_abs_2025[b]:>8.4f}" for b in BAND_NAMES))
        row = f"{c:>25} {len(pts):>4}  " + "  ".join(
            f"{band_means[b]:>8.3f}" if band_means[b] is not None else f"{'—':>8}" for b in BAND_NAMES
        )
        print(row)

    print(f"\nВсего точек: {total_pts}")

    # overall B05 ratio across all classes combined — the headline number
    all_b05 = []
    for c in CLASSES:
        if per_class_band_stats[c]:
            all_b05.extend([p["band_ratios"]["B05"] for p in by_class[c] if not np.isnan(p["band_ratios"]["B05"])])
    all_b05 = np.array(all_b05)
    b05_mean = float(all_b05.mean()) if all_b05.size else None
    b05_std = float(all_b05.std()) if all_b05.size else None

    print("\n" + "=" * 70)
    print("  ИТОГ — B05 ratio (2025/2023_cdse), все классы вместе")
    print("=" * 70)
    if b05_mean is not None:
        print(f"  mean={b05_mean:.3f}  std={b05_std:.3f}  (N={all_b05.size})")
        pct_off = abs(b05_mean - 1.0) * 100
        if pct_off <= 15:
            print(f"  OK — расхождение {pct_off:.1f}% в пределах ожидаемого (±5-15%, сезонность)")
        elif pct_off <= 25:
            print(f"  ПОГРАНИЧНО — расхождение {pct_off:.1f}%, чуть выше ожидаемого диапазона, но далеко от структурных ~35%")
        else:
            print(f"  ВНИМАНИЕ — расхождение {pct_off:.1f}%, близко к структурному сдвигу PC-vs-CDSE (~35%) — проверить offset/baseline")
    else:
        print("  Нет валидных точек для B05")

    result = {
        "generated_at": time.time(),
        "blocks_read": blocks_read,
        "total_points": total_pts,
        "per_class_band_stats": per_class_band_stats,
        "b05_overall": {"mean_ratio": b05_mean, "std": b05_std, "n": int(all_b05.size)},
    }
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nСохранено: {OUT_PATH}")


if __name__ == "__main__":
    main()
