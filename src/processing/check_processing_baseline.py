"""
GeoAI-TKO - src/processing/check_processing_baseline.py
=======================================================
ЗАДАЧА 1, Шаг 1.1-1.2 - диагностика Sentinel-2 processing baseline
для 2023_summer и 2025_summer, и эмпирическая проверка BOA_ADD_OFFSET.

Baseline >= 04.00 (с 2022-01-25) пишет DN со сдвигом:
    Reflectance = (DN + BOA_ADD_OFFSET) / 10000   (BOA_ADD_OFFSET обычно -1000)
Baseline < 04.00:
    Reflectance = DN / 10000                       (offset = 0)

Шаг 1: определить baseline продуктов каждого периода (из product_id, где
       есть паттерн N0XXX, и/или из STAC properties).
Шаг 2: эмпирически сверить reflectance на одних и тех же точках между
       периодами после применения соответствующего каждому baseline offset.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pyproj
import rasterio
import requests
from rasterio.transform import rowcol

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
COLLECTION = "sentinel-2-l2a"
CLOUD_COVER_MAX = 40

REPORT_PATH = Path(r"D:\data\availability_report.json")
MANIFEST_2025 = Path(r"D:\data\s2_2025_raw\manifest.json")
COG_2023 = Path(r"D:\data\mosaics\2023_summer\s2_mosaic_cog.tif")
COG_2025 = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")

BASELINE_RE = re.compile(r"_N(\d{4})_")


def baseline_from_product_id(pid: str) -> str:
    m = BASELINE_RE.search(pid)
    if not m:
        return "unknown"
    digits = m.group(1)  # e.g. "0511" -> "05.11"
    return f"{digits[:2]}.{digits[2:]}"


def baseline_value(baseline_str: str) -> float:
    try:
        return float(baseline_str)
    except ValueError:
        return -1.0


# ── Step 1.1a: 2025 baseline from manifest ──────────────────────────
def baselines_2025():
    manifest = json.loads(MANIFEST_2025.read_text(encoding="utf-8"))
    baselines = {}
    for tile, info in manifest["tiles"].items():
        pid = info["product_id"]
        baselines[tile] = baseline_from_product_id(pid)
    return baselines


# ── Step 1.1b: 2023 baseline - re-query STAC for best-per-tile summer 2023 ──
def fetch_summer(year: int):
    aoi = json.loads(REPORT_PATH.read_text(encoding="utf-8"))["aoi"]
    body = {
        "collections": [COLLECTION],
        "intersects": aoi,
        "datetime": f"{year}-06-01T00:00:00Z/{year}-08-31T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": CLOUD_COVER_MAX}},
        "limit": 200,
    }
    feats = []
    url = STAC_URL
    while True:
        r = requests.post(url, json=body, timeout=60)
        r.raise_for_status()
        d = r.json()
        feats.extend(d.get("features", []))
        nxt = next((l for l in d.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt["href"]
        body = nxt.get("body", body)
    return feats


def best_per_tile(master_tiles, feats):
    best = {}
    for f in feats:
        p = f["properties"]
        tile = (p.get("grid:code") or "").replace("MGRS-", "")
        if tile not in master_tiles:
            continue
        cc = p.get("eo:cloud_cover")
        if cc is None:
            continue
        cur = best.get(tile)
        if cur is None or cc < cur["cloud_cover"]:
            best[tile] = {"feature": f, "cloud_cover": cc}
    return best


def baselines_2023():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    master_tiles = set(report["master_tiles"])
    print("Запрашиваю STAC за лето 2023 (для диагностики baseline)...")
    feats = fetch_summer(2023)
    best = best_per_tile(master_tiles, feats)
    baselines = {}
    for tile, info in best.items():
        pid = info["feature"]["id"]
        props = info["feature"]["properties"]
        # try explicit STAC field first, fallback to id pattern
        b = props.get("processing:baseline") or props.get("s2:processing_baseline") \
            or props.get("processing:version")
        if not b:
            b = baseline_from_product_id(pid)
        baselines[tile] = str(b)
    return baselines, best


def print_baseline_summary(name, baselines):
    from collections import Counter
    c = Counter(baselines.values())
    print(f"\n{name} baseline distribution ({len(baselines)} tiles):")
    for b, n in sorted(c.items()):
        print(f"    {b}: {n} тайлов")
    return c


# ── Step 1.2: empirical offset check ────────────────────────────────
def empirical_check(n_points=15):
    print("\n" + "=" * 70)
    print("  Эмпирическая проверка BOA_ADD_OFFSET на реальных данных")
    print("=" * 70)

    if not COG_2025.exists():
        print(f"  ПРОПУСК: {COG_2025} ещё не существует (старая сборка).")
        return

    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32641", always_xy=True)

    with rasterio.open(COG_2023) as ds23, rasterio.open(COG_2025) as ds25:
        minx = max(ds23.bounds.left, ds25.bounds.left) + 20000
        maxx = min(ds23.bounds.right, ds25.bounds.right) - 20000
        miny = max(ds23.bounds.bottom, ds25.bounds.bottom) + 20000
        maxy = min(ds23.bounds.top, ds25.bounds.top) - 20000

        rng = np.random.default_rng(7)
        found = []
        attempts = 0
        while len(found) < n_points and attempts < 3000:
            attempts += 1
            x = rng.uniform(minx, maxx)
            y = rng.uniform(miny, maxy)
            r23, c23 = rowcol(ds23.transform, x, y)
            r25, c25 = rowcol(ds25.transform, x, y)
            if not (0 <= r23 < ds23.height and 0 <= c23 < ds23.width):
                continue
            if not (0 <= r25 < ds25.height and 0 <= c25 < ds25.width):
                continue
            v23 = ds23.read(window=rasterio.windows.Window(c23, r23, 1, 1)).flatten()
            v25 = ds25.read(window=rasterio.windows.Window(c25, r25, 1, 1)).flatten()
            if not (np.all(v23 > 0) and np.all(v25 > 0)):
                continue
            # crude water/cloud filter: NDWI-ish and brightness sanity via B02
            if v23[0] > 5000 or v25[0] > 5000:  # likely cloud/snow
                continue
            found.append((x, y, v23.tolist(), v25.tolist()))

    if not found:
        print("  Не нашёл валидных совпадающих точек для сравнения.")
        return

    print(f"  Найдено {len(found)} точек (B02,B03,B04,B05,B08,B8A,B11 DN)\n")
    OFFSET_2023 = 0       # baseline < 04.00 предполагается
    OFFSET_2025 = -1000    # baseline >= 04.00 (N0511) предполагается

    print(f"  {'B02_2023':>10} {'B02_2025':>10} | {'refl23':>8} {'refl25':>8} {'ratio':>7}")
    ratios = []
    for x, y, v23, v25 in found:
        refl23 = (v23[0] + OFFSET_2023) / 10000.0
        refl25 = (v25[0] + OFFSET_2025) / 10000.0
        ratio = refl25 / refl23 if refl23 > 0 else float("nan")
        ratios.append(ratio)
        print(f"  {v23[0]:>10} {v25[0]:>10} | {refl23:>8.4f} {refl25:>8.4f} {ratio:>7.2f}")

    ratios = np.array(ratios)
    print(f"\n  Ratio reflectance(2025)/reflectance(2023) по B02:")
    print(f"    среднее={ratios.mean():.2f}  медиана={np.median(ratios):.2f}  std={ratios.std():.2f}")
    print("  Если offset-гипотеза верна: ratio должен быть близко к 1.0 (в пределах ~0.5-2x")
    print("  из-за 2 лет разницы), а НЕ систематически ~1.7-2x как было с сырыми DN.")

    # also show raw-DN ratio without offset correction for comparison
    raw_ratios = np.array([v25[0] / v23[0] for _, _, v23, v25 in found])
    print(f"\n  Для сравнения — ratio БЕЗ offset-коррекции (сырые DN, как в текущем QA):")
    print(f"    среднее={raw_ratios.mean():.2f}  медиана={np.median(raw_ratios):.2f}")


def main():
    print("=" * 70)
    print("  Диагностика processing baseline: 2023_summer vs 2025_summer")
    print("=" * 70)

    b2025 = baselines_2025()
    c2025 = print_baseline_summary("2025_summer", b2025)

    b2023, best2023 = baselines_2023()
    c2023 = print_baseline_summary("2023_summer (реконструировано по STAC, best-per-tile)", b2023)

    print("\n" + "=" * 70)
    print("  ИТОГ Шага 1.1")
    print("=" * 70)
    dom2023 = c2023.most_common(1)[0][0] if c2023 else "unknown"
    dom2025 = c2025.most_common(1)[0][0] if c2025 else "unknown"
    print(f"  2023_summer baseline (доминирующий): {dom2023}")
    print(f"  2025_summer baseline (доминирующий): {dom2025}")
    v2023 = baseline_value(dom2023)
    v2025 = baseline_value(dom2025)
    if 0 <= v2023 < 4.0:
        print(f"  -> 2023: baseline < 04.00 => BOA_ADD_OFFSET = 0 (гипотеза)")
    elif v2023 >= 4.0:
        print(f"  -> 2023: baseline >= 04.00 => BOA_ADD_OFFSET = -1000 (!!) пересмотреть гипотезу")
    if v2025 >= 4.0:
        print(f"  -> 2025: baseline >= 04.00 => BOA_ADD_OFFSET = -1000 (гипотеза)")

    empirical_check()


if __name__ == "__main__":
    main()
