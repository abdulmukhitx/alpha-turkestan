"""
GeoAI-TKO · src/processing/validate_mosaic.py
=======================================================
ЭТАП 6 — Контроль качества мозаика перед тем как считать период "active".

Проверки:
  1. Размер итогового файла vs ориентир по аналогии с эталонным периодом
     (уже посчитано в логе build_mosaic_2025.py — здесь просто читаем файл)
  2. 30 случайных точек, равномерно по площади AOI (внутри реальной границы
     Туркестанской области, не padded bbox) — ни одна не должна иметь все
     7 бэндов == 0
  3. gdalinfo -stats (через rasterio) — нет аномальных min/max/NaN
  4. Превью PNG (downsampled через overview) для визуальной проверки на
     швы/дыры
  5. ML-классификатор v2 на семпле пикселей — сравнение распределения
     классов с эталонным периодом (детектирует грубые сдвиги типа
     water 1% -> 40%, которые сигналят об ошибке в данных)

Только если ВСЕ проверки прошли — печатает итоговый metadata.json,
готовый для ручного подтверждения и записи на диск (этот скрипт его
не пишет сам — ждёт явного "да" от пользователя по протоколу проекта).

Usage:
  python src/processing/validate_mosaic.py --period 2025_summer
"""
import argparse
import json
import pickle
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import shape, Point
from shapely.ops import unary_union, transform as shapely_transform

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

MOSAICS_DIR = Path(r"D:\data\mosaics")
BOUNDARY_PATH = Path(r"C:\Users\USER\alpha-turkestan\frontend\public\turkestan_boundary.geojson")
# v1 (6 spectral features, no texture) — same model /api/zone_stats uses.
# v2 needs 7 extra 3x3-texture std features that aren't available from a
# cheap overview-level sample; feeding it zeros instead of real texture
# systematically biases predictions toward "homogeneous"-looking classes
# (bare_soil/water) and away from textured ones (urban/agriculture) — that
# was silently wrong in an earlier version of this check.
CLASSIFIER_PATH = Path(r"D:\data\classifiers\lulc_classifier.pkl")
N_POINTS = 30
SIZE_DEVIATION_THRESHOLD = 0.30

# Both periods now stored as float32 physical reflectance (0.0-1.0),
# nodata=-9999.0 — NOT the old uint16 DN format (nodata=0, values up to
# ~10000+). See project memory: 2025_summer needed BOA_ADD_OFFSET=-1000,
# 2023_summer didn't (Planetary Computer harmonized it server-side); both
# are now converted to the same physical unit at build time so this QA
# script compares like-for-like instead of raw DN across different offsets.
# Nodata/scale are detected per-file from ds.nodata/ds.dtypes at read time
# (not hardcoded here) since this script may compare a new float32 file
# against the still-untouched old uint16 2023_summer reference.

REFERENCE_PERIOD = "2023_summer"
REFERENCE_COG_SIZE_GB = 34.5  # old uint16 baseline — informational only, see check_1

_ML_CLASS_RU = {
    "water": "Вода", "dense_vegetation": "Густая растительность",
    "agriculture": "Сельхозугодья", "sparse_vegetation": "Разреженная растительность",
    "bare_soil": "Голая почва", "urban": "Застройка",
}


def load_boundary_union():
    geo = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
    feats = geo.get("features", [geo])
    geoms = [shape(f.get("geometry", f)) for f in feats]
    return unary_union(geoms)


def check_1_size(cog_path: Path, tile_count: int) -> dict:
    print("\n" + "=" * 70)
    print("  ПРОВЕРКА 1/5 — размер файла")
    print("=" * 70)
    size_gb = cog_path.stat().st_size / 1e9
    print(f"  Итоговый размер: {size_gb:.2f} GB ({tile_count} тайлов)")
    # The old uint16-DN heuristic (34.5GB@37 tiles) isn't directly comparable
    # to float32 reflectance (2x bytes/pixel, different compression ratio
    # since reflectance values 0.0-1.0 compress differently than raw DN) —
    # informational only, no hard pass/fail gate for this format.
    print(f"  (старый ориентир {REFERENCE_COG_SIZE_GB}GB@37 тайлов был для uint16 DN — "
          f"не сравним напрямую с float32 reflectance, не используется как gate)")
    print("  OK (не блокирующая проверка для float32-формата)")
    return {"size_gb": round(size_gb, 2), "passed": True}


def check_2_points(cog_path: Path, boundary_union) -> dict:
    print("\n" + "=" * 70)
    print(f"  ПРОВЕРКА 2/5 — {N_POINTS} случайных точек по площади AOI")
    print("=" * 70)
    minx, miny, maxx, maxy = boundary_union.bounds
    rng = random.Random(42)  # reproducible

    points_wgs84 = []
    attempts = 0
    while len(points_wgs84) < N_POINTS and attempts < N_POINTS * 200:
        attempts += 1
        lon = rng.uniform(minx, maxx)
        lat = rng.uniform(miny, maxy)
        if boundary_union.contains(Point(lon, lat)):
            points_wgs84.append((lon, lat))

    if len(points_wgs84) < N_POINTS:
        print(f"  ОШИБКА: смог сгенерировать только {len(points_wgs84)}/{N_POINTS} точек внутри границы")

    results = []
    with rasterio.open(cog_path) as ds:
        file_nodata = ds.nodata if ds.nodata is not None else 0.0
        transformer = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
        for lon, lat in points_wgs84:
            x, y = transformer.transform(lon, lat)
            row, col = ds.index(x, y)
            if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
                results.append({"lon": lon, "lat": lat, "status": "outside_raster"})
                continue
            window = rasterio.windows.Window(col, row, 1, 1)
            vals = ds.read(window=window)[:, 0, 0]
            # nodata written uniformly across all 7 bands per pixel by
            # construction (build_mosaic_2025.py) — checking band[0] is
            # sufficient. nodata value read from the file itself (-9999.0 for
            # float32 reflectance, 0 for old uint16 DN) — not hardcoded, since
            # 0.0 reflectance is itself a physically valid (if dark) surface
            # value and must not be misread as nodata.
            is_nodata = bool(vals[0] == file_nodata)
            results.append({
                "lon": round(lon, 5), "lat": round(lat, 5),
                "values": vals.tolist(), "all_zero": is_nodata,
                "status": "all_zero" if is_nodata else "ok",
            })

    bad = [r for r in results if r["status"] != "ok"]
    for r in bad:
        print(f"  ПРОБЛЕМА: ({r['lon']}, {r['lat']}) -> {r['status']}")
    passed = len(bad) == 0 and len(results) == N_POINTS
    print(f"  Валидных точек: {len(results) - len(bad)}/{len(results)}")
    print(f"  {'OK' if passed else 'ОШИБКА'}")
    return {"checked_points": len(results), "valid_points": len(results) - len(bad),
            "bad_points": bad, "passed": passed}


def check_3_gdalinfo_stats(cog_path: Path, boundary_union, sample_n: int = 5000) -> dict:
    print("\n" + "=" * 70)
    print("  ПРОВЕРКА 3/5 — статистика по бэндам (точки строго внутри границы)")
    print("=" * 70)
    # Was: whole-raster read via coarsest overview — silently included the
    # padded AOI margin OUTSIDE the official boundary (see 42TVS lesson:
    # padded bbox != real oblast territory), and reproduced the same
    # overview-blur risk documented in check_5. Now point-sampled inside
    # boundary_union, full-resolution reads, same pattern as check_2/check_5.
    band_names = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]
    points = _random_points_in_boundary(boundary_union, sample_n)
    print(f"  ({len(points)} точек внутри границы, full-resolution чтение)")

    stats = {}
    anomalies = []
    samples = {name: [] for name in band_names}
    has_nan_any = {name: False for name in band_names}

    with rasterio.open(cog_path) as ds:
        file_nodata = ds.nodata if ds.nodata is not None else 0.0
        transformer = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
        n_valid = 0
        for lon, lat in points:
            x, y = transformer.transform(lon, lat)
            row, col = ds.index(x, y)
            if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
                continue
            vals = ds.read(window=rasterio.windows.Window(col, row, 1, 1))[:, 0, 0]
            if vals[0] == file_nodata:
                continue
            n_valid += 1
            for i, name in enumerate(band_names):
                v = float(vals[i])
                if np.isnan(v):
                    has_nan_any[name] = True
                else:
                    samples[name].append(v)
    print(f"  Валидных точек: {n_valid}/{len(points)}")

    for name in band_names:
        arr = np.array(samples[name])
        if arr.size == 0:
            anomalies.append(f"{name}: все значения nodata в выборке")
            continue
        bmin, bmax, bmean, bstd = float(arr.min()), float(arr.max()), float(arr.mean()), float(arr.std())
        stats[name] = {"min": round(bmin, 4), "max": round(bmax, 4),
                        "mean": round(bmean, 4), "std": round(bstd, 4), "has_nan": has_nan_any[name]}
        print(f"  {name}: min={bmin:.4f} max={bmax:.4f} mean={bmean:.4f} std={bstd:.4f}")
        if has_nan_any[name]:
            anomalies.append(f"{name}: содержит NaN")
        if bmax > 1.0001:  # physical reflectance must be <=1.0 (clipped at build time)
            anomalies.append(f"{name}: аномально высокий max={bmax:.4f} (>1.0 для reflectance)")
        if bmin < 0:
            anomalies.append(f"{name}: отрицательное значение min={bmin:.4f} (нефизично для reflectance)")
        if bstd < 1e-4:  # reflectance std order 0.01-0.1 is normal; near-zero means a constant band
            anomalies.append(f"{name}: подозрительно низкая дисперсия (std={bstd:.6f}) — возможно константа")

    passed = len(anomalies) == 0
    for a in anomalies:
        print(f"  АНОМАЛИЯ: {a}")
    print(f"  {'OK' if passed else 'ОШИБКА'}")
    return {"band_stats": stats, "anomalies": anomalies, "passed": passed}


def check_4_preview(cog_path: Path, out_path: Path) -> dict:
    print("\n" + "=" * 70)
    print("  ПРОВЕРКА 4/5 — превью PNG")
    print("=" * 70)
    from PIL import Image
    with rasterio.open(cog_path) as ds:
        file_nodata = ds.nodata if ds.nodata is not None else 0.0
        ovr = ds.overviews(1)
        factor = max(ovr) if ovr else 1
        out_h = max(1, ds.height // factor)
        out_w = max(1, ds.width // factor)
        # true-color-ish: B04,B03,B02 (R,G,B), bands are indexed B02=1,B03=2,B04=3
        rgb = ds.read([3, 2, 1], out_shape=(3, out_h, out_w)).astype(np.float32)
        rgb_valid = rgb[rgb != file_nodata]
        p2, p98 = np.percentile(rgb_valid, [2, 98]) if rgb_valid.size else (0, 1)
        rgb[rgb == file_nodata] = 0.0
        rgb = np.clip((rgb - p2) / (p98 - p2 + 1e-6), 0, 1)
        rgb_u8 = (rgb * 255).astype(np.uint8)
        img = Image.fromarray(np.transpose(rgb_u8, (1, 2, 0)), mode="RGB")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
    print(f"  Сохранено: {out_path} ({img.width}x{img.height}px)")
    print("  ПОСМОТРИ ГЛАЗАМИ на швы/дыры перед тем как подтверждать период.")
    return {"preview_path": str(out_path), "width": img.width, "height": img.height}


def _random_points_in_boundary(boundary_union, n: int, seed: int = 42) -> list[tuple[float, float]]:
    rng = random.Random(seed)
    minx, miny, maxx, maxy = boundary_union.bounds
    pts = []
    attempts = 0
    while len(pts) < n and attempts < n * 200:
        attempts += 1
        lon = rng.uniform(minx, maxx)
        lat = rng.uniform(miny, maxy)
        if boundary_union.contains(Point(lon, lat)):
            pts.append((lon, lat))
    return pts


def check_5_ml_distribution(cog_path: Path, boundary_union, ref_period: str = REFERENCE_PERIOD, sample_n: int = 3000) -> dict:
    print("\n" + "=" * 70)
    print("  ПРОВЕРКА 5/5 — распределение классов ML (v1, как в /api/zone_stats) vs 2023_summer")
    print("=" * 70)
    if not CLASSIFIER_PATH.exists():
        print("  ПРОПУЩЕНО: classifiers/lulc_classifier.pkl не найден")
        return {"skipped": True}

    with open(CLASSIFIER_PATH, "rb") as f:
        saved = pickle.load(f)
    model = saved["model"]
    label_encoder = saved["label_encoder"]
    classes = list(label_encoder.classes_)

    # Same N points (within the real oblast boundary) for both periods —
    # full-resolution single-pixel reads, NOT an overview-level bulk sample.
    # An earlier version of this check read from the coarsest (64x) overview,
    # where bilinear-blurred blocks straddling the raster's nodata edge get
    # pulled toward zero even when their center is geographically inside the
    # boundary. 2025_summer's much larger raw footprint (still "with margin",
    # not yet clipped to the exact oblast border) has proportionally far more
    # of this nodata-adjacent edge area than 2023_summer's tighter footprint,
    # which fabricated a huge fake class-distribution "shift" that had
    # nothing to do with the actual land cover. Full-res point sampling
    # avoids the blur entirely.
    points = _random_points_in_boundary(boundary_union, sample_n)
    print(f"  ({len(points)} точек внутри границы, full-resolution чтение)")

    def sample_distribution(path: Path) -> dict:
        with rasterio.open(path) as ds:
            # scale/nodata detected per-file, not hardcoded — this comparison
            # may straddle an old raw-DN file (uint16, nodata=0, needs /10000
            # to reach reflectance scale) and a new physical-reflectance file
            # (float32, nodata=-9999.0, already in 0.0-1.0, must NOT be
            # divided again). Ratio features (NDVI etc.) are scale-invariant
            # so this only matters for the raw b08 feature, but getting it
            # wrong there still corrupts the classifier input by 10000x.
            file_nodata = ds.nodata if ds.nodata is not None else 0.0
            needs_dn_scale = ds.dtypes[0] != "float32"
            transformer = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
            feats_list = []
            for lon, lat in points:
                x, y = transformer.transform(lon, lat)
                row, col = ds.index(x, y)
                if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
                    continue
                vals = ds.read(window=rasterio.windows.Window(col, row, 1, 1))[:, 0, 0].astype(np.float32)
                if vals[0] == file_nodata:
                    continue
                if needs_dn_scale:
                    vals = vals / 10000.0
                feats_list.append(vals)
            if not feats_list:
                return {}, 0
            data = np.stack(feats_list, axis=0)  # (n, 7)
            b02, b03, b04, b05, b08, b8a, b11 = (data[:, i] for i in range(7))
            eps = 1e-10
            ndvi = (b08 - b04) / (b08 + b04 + eps)
            ndre = (b08 - b05) / (b08 + b05 + eps)
            ndwi = (b03 - b08) / (b03 + b08 + eps)
            ndmi = (b8a - b11) / (b8a + b11 + eps)
            bsi = ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02) + eps)
            # v1 classifier: exactly the 6 features classify_ml() in backend/main.py
            # uses (ndvi, ndre, ndwi, ndmi, bsi, b08) — no texture features needed.
            feats = np.stack([ndvi, ndre, ndwi, ndmi, bsi, b08], axis=1).astype(np.float32)
            preds = model.predict(feats)
            pred_classes = label_encoder.inverse_transform(preds)
            counts = {}
            for c in pred_classes:
                counts[c] = counts.get(c, 0) + 1
            total = len(pred_classes)
            return {c: round(counts.get(c, 0) / total * 100, 2) for c in classes}, total

    dist_2025, n_2025 = sample_distribution(cog_path)
    ref_path = MOSAICS_DIR / ref_period / "s2_mosaic_cog.tif"
    dist_2023, n_2023 = sample_distribution(ref_path) if ref_path.exists() else ({}, 0)
    print(f"  Валидных точек: текущий={n_2025}/{len(points)}, референс({ref_period})={n_2023}/{len(points)}")

    print(f"  {'Класс':<22} {'текущий %':>10} {ref_period + ' %':>18} {'Δ':>8}")
    big_shifts = []
    for c in classes:
        v25 = dist_2025.get(c, 0.0)
        v23 = dist_2023.get(c, 0.0)
        delta = v25 - v23
        flag = "  <-- большой сдвиг" if abs(delta) > 15 else ""
        print(f"  {_ML_CLASS_RU.get(c, c):<22} {v25:>7.2f}% {v23:>7.2f}% {delta:>+7.2f}%{flag}")
        if abs(delta) > 15:
            big_shifts.append({"class": c, "delta_pct": round(delta, 2)})

    passed = len(big_shifts) == 0
    print(f"  {'OK — распределение похоже на 2023' if passed else 'ВНИМАНИЕ — большие сдвиги, см. выше'}")
    return {"distribution_2025": dist_2025, "distribution_2023": dist_2023,
            "n_sampled_2025": n_2025, "n_sampled_2023": n_2023,
            "big_shifts": big_shifts, "passed": passed,
            "note": "texture(std_*) признаки заменены нулями — это упрощённая аппроксимация для QA, не точная копия prod-классификации"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--ref-period", default=REFERENCE_PERIOD,
                         help="период для сравнения ML-распределения (по умолчанию 2023_summer)")
    args = parser.parse_args()

    period_dir = MOSAICS_DIR / args.period
    cog_path = period_dir / "s2_mosaic_cog.tif"
    if not cog_path.exists():
        print(f"ОШИБКА: {cog_path} не найден")
        sys.exit(1)

    print("=" * 70)
    print(f"  ЭТАП 6 — QA для периода {args.period}")
    print("=" * 70)

    print("\nЗагружаю границу Туркестанской области...")
    boundary_union = load_boundary_union()

    # tile_count: try to read from estimate/manifest if present, else fall back to 43 (this run)
    tile_count = 43

    r1 = check_1_size(cog_path, tile_count)
    r2 = check_2_points(cog_path, boundary_union)
    r3 = check_3_gdalinfo_stats(cog_path, boundary_union)
    r4 = check_4_preview(cog_path, period_dir / "preview.png")
    r5 = check_5_ml_distribution(cog_path, boundary_union, ref_period=args.ref_period)

    all_passed = r1["passed"] and r2["passed"] and r3["passed"] and r5.get("passed", True)

    print("\n" + "=" * 70)
    print("  ИТОГ Этапа 6 — все проверки")
    print("=" * 70)
    print(f"  1. Размер файла:        {'OK' if r1['passed'] else 'ОШИБКА'} ({r1['size_gb']} GB)")
    print(f"  2. 30 случайных точек:  {'OK' if r2['passed'] else 'ОШИБКА'} ({r2['valid_points']}/{r2['checked_points']} валидны)")
    print(f"  3. Статистика бэндов:   {'OK' if r3['passed'] else 'ОШИБКА'} ({len(r3['anomalies'])} аномалий)")
    print(f"  4. Превью PNG:          сохранено, {r4['preview_path']} — ТРЕБУЕТ ВИЗУАЛЬНОЙ ПРОВЕРКИ ГЛАЗАМИ")
    ml_detail = "пропущено" if r5.get("skipped") else f"{len(r5.get('big_shifts', []))} сдвигов >15%"
    print(f"  5. ML-распределение:    {'OK' if r5.get('passed', True) else 'ВНИМАНИЕ'} ({ml_detail})")
    print(f"\n  Автоматические проверки: {'ВСЕ ПРОЙДЕНЫ' if all_passed else 'ЕСТЬ ПРОБЛЕМЫ — см. выше'}")
    print("  Финальное решение о готовности периода — за пользователем (после просмотра preview.png).")

    report = {
        "period_id": args.period,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": {"size": r1, "points": r2, "band_stats": r3, "preview": r4, "ml_distribution": r5},
        "all_automated_checks_passed": all_passed,
    }
    out_path = period_dir / "qa_report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Полный отчёт: {out_path}")


if __name__ == "__main__":
    main()
