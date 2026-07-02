"""
GeoAI-TKO - src/processing/check_offset_stratified.py
=======================================================
Расширение эмпирической проверки BOA_ADD_OFFSET (Задача 1, Шаг 1.2) —
100+ точек, стратифицированных по типу покрова (классификатор v1 на
2023_summer как источник истины для лейбла класса), отдельно посчитан
ratio reflectance(2025, offset=-1000) / reflectance(2023, offset=0)
внутри каждого класса.

Быстрая версия: читает случайные БЛОКИ (не отдельные пиксели) — один
блочный read декомпрессирует один DEFLATE-блок COG и даёт тысячи
кандидатных пикселей сразу, вместо одного пикселя за read.
"""
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

COG_2023 = Path(r"D:\data\mosaics\2023_summer\s2_mosaic_cog.tif")
COG_2025 = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
CLASSIFIER_PATH = Path(r"D:\data\classifiers\lulc_classifier.pkl")

OFFSET_2023 = 0
OFFSET_2025 = -1000
N_TARGET_PER_CLASS = 25
BLOCK = 512
CLASSES = ["water", "dense_vegetation", "agriculture", "sparse_vegetation", "bare_soil", "urban"]


def features_batch(arr):
    # arr shape (7, H, W): B02,B03,B04,B05,B08,B8A,B11
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

    minx = max(ds23.bounds.left, ds25.bounds.left) + 20000
    maxx = min(ds23.bounds.right, ds25.bounds.right) - 20000
    miny = max(ds23.bounds.bottom, ds25.bounds.bottom) + 20000
    maxy = min(ds23.bounds.top, ds25.bounds.top) - 20000

    rng = np.random.default_rng(11)
    by_class = {c: [] for c in CLASSES}
    blocks_read = 0
    max_blocks = 400

    while blocks_read < max_blocks and any(len(v) < N_TARGET_PER_CLASS for v in by_class.values()):
        blocks_read += 1
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        r23, c23 = rowcol(ds23.transform, x, y)
        if not (0 <= r23 < ds23.height - BLOCK and 0 <= c23 < ds23.width - BLOCK):
            continue

        block23 = ds23.read(window=Window(c23, r23, BLOCK, BLOCK))  # (7,H,W)
        valid23 = np.all(block23 > 0, axis=0) & (block23[0] <= 3500)
        if not valid23.any():
            continue

        # corresponding block in 2025 by real-world coords of each pixel corner
        left_x, top_y = xy(ds23.transform, r23, c23, offset="ul")
        r25, c25 = rowcol(ds25.transform, left_x, top_y)
        if not (0 <= r25 < ds25.height - BLOCK and 0 <= c25 < ds25.width - BLOCK):
            continue
        block25 = ds25.read(window=Window(c25, r25, BLOCK, BLOCK))
        valid25 = np.all(block25 > 0, axis=0) & (block25[0] <= 3500)

        valid = valid23 & valid25
        n_valid = valid.sum()
        if n_valid == 0:
            continue

        feats = features_batch(block23)[valid]  # (N,6)
        preds = clf.predict(feats)

        v23 = block23[:, valid]  # (7,N)
        v25 = block25[:, valid]
        refl23_b02 = (v23[0].astype(np.int32) + OFFSET_2023) / 10000.0
        refl25_b02 = (v25[0].astype(np.int32) + OFFSET_2025) / 10000.0
        ratios = np.divide(refl25_b02, refl23_b02, out=np.full_like(refl23_b02, np.nan), where=refl23_b02 != 0)

        for i in range(len(preds)):
            cname = label_map[int(preds[i])]
            if cname in by_class and len(by_class[cname]) < N_TARGET_PER_CLASS:
                by_class[cname].append(float(ratios[i]))

        elapsed = time.time() - t0
        counts = {c: len(v) for c, v in by_class.items()}
        print(f"  [блок {blocks_read}/{max_blocks}, {elapsed:.0f}s] найдено: {counts}", flush=True)

    ds23.close()
    ds25.close()

    print(f"\nБлоков прочитано: {blocks_read}, время: {time.time()-t0:.0f}s")
    total_pts = 0
    all_ratios = []
    print(f"\n{'Класс':>20} {'N':>5} {'mean_ratio':>11} {'median':>8} {'std':>7}")
    for c in CLASSES:
        r = np.array(by_class[c])
        total_pts += len(r)
        if len(r) == 0:
            print(f"{c:>20} {'0':>5}      (нет точек)")
            continue
        all_ratios.extend(r.tolist())
        print(f"{c:>20} {len(r):>5} {r.mean():>11.3f} {np.median(r):>8.3f} {r.std():>7.3f}")

    all_ratios = np.array(all_ratios)
    print(f"\nВсего точек: {total_pts}")
    if len(all_ratios):
        print(f"ОБЩИЙ ratio: mean={all_ratios.mean():.3f} median={np.median(all_ratios):.3f} std={all_ratios.std():.3f}")
    print("\nЕсли offset-гипотеза верна для ВСЕХ типов покрова — ratio должен быть")
    print("стабильно близко к 1.0 (в пределах ~0.7-1.4) в каждом классе отдельно,")
    print("без систематического отклонения именно для одного/нескольких типов.")


if __name__ == "__main__":
    main()
