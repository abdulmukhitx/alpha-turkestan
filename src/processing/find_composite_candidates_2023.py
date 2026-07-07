"""
GeoAI-TKO · src/processing/find_composite_candidates_2023.py
=======================================================
ЭТАП 1 (2023_summer CDSE rebuild) — STAC-поиск + orbit-diverse top-3 +
оценка объёма скачивания, ДО любого скачивания. Тот же источник (CDSE) и
та же логика, что 2025_summer, но с учётом уроков 2025 с самого начала:
top-3 orbit-diverse отбирается СРАЗУ (не primary-then-fill-later, как было
в 2025 по историческим причинам) — потому что известно заранее, что чистый
top-3-by-cloud систематически бьёт по orbit-diversity (см. find_composite_
candidates.py, откуда сюда импортируется select_orbit_diverse_top_n).

Не скачивает ничего. Пишет:
  D:\\data\\s2_2023_cdse_raw\\composite_candidates.json — top-3 план на тайл,
    готов к прямому использованию скачивающим скриптом Этапа 2
  D:\\data\\download_estimate_2023_cdse.json — оценка объёма (primary+fill1+fill2)

Usage:
  python src/processing/find_composite_candidates_2023.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from find_composite_candidates import select_orbit_diverse_top_n  # noqa: E402 — reuse, do not reimplement

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
COLLECTION = "sentinel-2-l2a"
CLOUD_COVER_MAX = 40
YEAR = 2023
TOP_N = 3

NEEDED_ASSETS = ["B02_10m", "B03_10m", "B04_10m", "B08_10m", "B05_20m", "B8A_20m", "B11_20m"]

REPORT_PATH = Path(r"D:\data\availability_report.json")
OUT_CANDIDATES_PATH = Path(r"D:\data\s2_2023_cdse_raw\composite_candidates.json")
OUT_ESTIMATE_PATH = Path(r"D:\data\download_estimate_2023_cdse.json")

# Real 2025 numbers for reference (NOT the primary-only Stage-3 estimate
# quoted historically — the actual totals once orbit-diverse fills were
# included): 43 tiles, ~59GB raw JP2 on disk (primary+fill1+fill2),
# final float32 reflectance COG = 69.4GB actual (D:\data\mosaics\2025_summer\s2_mosaic_cog.tif).
REF_2025_TILE_COUNT = 43
REF_2025_RAW_GB = 59.0
REF_2025_COG_GB = 69.4


def fetch_summer(year: int, aoi: dict):
    body = {
        "collections": [COLLECTION],
        "intersects": aoi,
        "datetime": f"{year}-06-01T00:00:00Z/{year}-08-31T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": CLOUD_COVER_MAX}},
        "limit": 200,
    }
    feats = []
    url = STAC_URL
    # Accept-Encoding without "br": this venv's brotlicffi decoder throws
    # DecodeError on the STAC server's brotli-compressed responses.
    headers = {"Accept-Encoding": "gzip, deflate"}
    while True:
        r = requests.post(url, json=body, headers=headers, timeout=60)
        r.raise_for_status()
        d = r.json()
        feats.extend(d.get("features", []))
        nxt = next((l for l in d.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt["href"]
        body = nxt.get("body", body)
    return feats


def group_by_tile(master_tiles, feats):
    by_tile = {t: [] for t in master_tiles}
    for f in feats:
        p = f["properties"]
        tile = (p.get("grid:code") or "").replace("MGRS-", "")
        if tile not in by_tile:
            continue
        cc = p.get("eo:cloud_cover")
        if cc is None:
            continue
        by_tile[tile].append({
            "product_id": f["id"],
            "cloud_cover": round(cc, 2),
            "datetime": p.get("datetime"),
            "relative_orbit": str(p.get("sat:relative_orbit", "")),
            "processing_baseline": p.get("processing:baseline"),
            "assets": f.get("assets", {}),
        })
    for tile in by_tile:
        by_tile[tile].sort(key=lambda x: x["cloud_cover"])
    return by_tile


def asset_bytes(assets: dict) -> tuple[int, list[str]]:
    total = 0
    missing = []
    for key in NEEDED_ASSETS:
        a = assets.get(key)
        size = a.get("file:size") if a else None
        if size is None:
            missing.append(key)
            continue
        total += size
    return total, missing


def main():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    master_tiles = report["master_tiles"]
    aoi = report["aoi"]

    print("=" * 70)
    print(f"  Этап 1 (2023_summer CDSE) — STAC-поиск + orbit-diverse top-{TOP_N} на {len(master_tiles)} тайлов")
    print("=" * 70)
    print(f"\nЗапрашиваю STAC ({YEAR}-06-01 .. {YEAR}-08-31, cloud_cover < {CLOUD_COVER_MAX}%)...")
    feats = fetch_summer(YEAR, aoi)
    print(f"Всего продуктов найдено: {len(feats)}")

    by_tile = group_by_tile(master_tiles, feats)

    result = {"year": YEAR, "generated_at": datetime.now(timezone.utc).isoformat(), "tiles": {}}
    per_tile_estimate = []
    total_bytes = 0
    low_orbit_diversity_tiles = []
    missing_primary_tiles = []

    print(f"\n{'Тайл':>8} {'Всего':>6} {'#Orbits':>8} {'Primary CC':>11} {'Secondary CC':>13} {'Tertiary CC':>12} {'GB(top3)':>9}")
    for tile in sorted(master_tiles):
        candidates = by_tile.get(tile, [])
        top = select_orbit_diverse_top_n(candidates, TOP_N)

        if not top:
            missing_primary_tiles.append(tile)

        n_unique_orbits = len({c["relative_orbit"] for c in top})
        if n_unique_orbits < 2 and len(top) >= 2:
            low_orbit_diversity_tiles.append(tile)

        tile_bytes = 0
        tile_missing_assets = {}
        for c in top:
            b, missing = asset_bytes(c["assets"])
            tile_bytes += b
            if missing:
                tile_missing_assets[c["product_id"]] = missing
        total_bytes += tile_bytes

        entry = {
            "n_total_candidates": len(candidates),
            "candidates": [
                {"product_id": c["product_id"], "cloud_cover": c["cloud_cover"],
                 "datetime": c["datetime"], "relative_orbit": c["relative_orbit"],
                 "processing_baseline": c["processing_baseline"]}
                for c in top
            ],
            "n_unique_orbits_in_top3": n_unique_orbits,
            "estimated_bytes_top3": tile_bytes,
            "missing_assets": tile_missing_assets,
        }
        result["tiles"][tile] = entry
        per_tile_estimate.append({"tile": tile, "gb": round(tile_bytes / 1e9, 3)})

        cc = [str(c["cloud_cover"]) for c in top] + ["-"] * (3 - len(top))
        flag = "  <-- НИЗКОЕ orbit-разнообразие" if tile in low_orbit_diversity_tiles else ""
        print(f"{tile:>8} {len(candidates):>6} {n_unique_orbits:>8} {cc[0]:>11} {cc[1]:>13} {cc[2]:>12} "
              f"{tile_bytes/1e9:>8.2f}{flag}")

    total_gb = total_bytes / 1e9
    scale = len(master_tiles) / REF_2025_TILE_COUNT
    ref_scaled_raw_gb = REF_2025_RAW_GB * scale
    ref_scaled_cog_gb = REF_2025_COG_GB * scale

    print("\n" + "=" * 70)
    print("  ИТОГ Этапа 1")
    print("=" * 70)
    print(f"  Тайлов с >=1 кандидатом: {len(master_tiles) - len(missing_primary_tiles)}/{len(master_tiles)}")
    if missing_primary_tiles:
        print(f"  ОШИБКА: нет НИ ОДНОГО продукта для тайлов: {missing_primary_tiles}")
    print(f"  Тайлов с n_unique_orbits_in_top3 < 2: {len(low_orbit_diversity_tiles)} "
          f"(зафиксировано для diagnostic-проверки, не блокирует Этап 1): {low_orbit_diversity_tiles}")
    print(f"\n  Оценка скачивания (top-3 orbit-diverse, все 7 бэндов x до 3 продуктов/тайл): {total_gb:.2f} GB")
    print(f"  Референс 2025 (43 тайла, фактически): {REF_2025_RAW_GB} GB скачано -> {REF_2025_COG_GB} GB итоговый COG")
    print(f"  Экстраполяция на {len(master_tiles)} тайлов по аналогии: ~{ref_scaled_raw_gb:.1f} GB скачивания, "
          f"~{ref_scaled_cog_gb:.1f} GB итоговый COG")

    top5 = sorted(per_tile_estimate, key=lambda x: -x["gb"])[:5]
    print("\n  5 самых тяжёлых тайлов (top-3 суммарно):")
    for row in top5:
        print(f"    {row['tile']}: {row['gb']:.2f} GB")

    result["summary"] = {
        "total_estimated_bytes": total_bytes,
        "total_estimated_gb": round(total_gb, 3),
        "missing_primary_tiles": missing_primary_tiles,
        "low_orbit_diversity_tiles": low_orbit_diversity_tiles,
        "reference_2025": {"tile_count": REF_2025_TILE_COUNT, "raw_gb": REF_2025_RAW_GB, "cog_gb": REF_2025_COG_GB},
        "extrapolated_raw_gb": round(ref_scaled_raw_gb, 1),
        "extrapolated_cog_gb": round(ref_scaled_cog_gb, 1),
    }

    OUT_CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_CANDIDATES_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    OUT_ESTIMATE_PATH.write_text(json.dumps(result["summary"] | {"per_tile": per_tile_estimate}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Сохранено: {OUT_CANDIDATES_PATH}")
    print(f"  Сохранено: {OUT_ESTIMATE_PATH}")

    if missing_primary_tiles:
        sys.exit(1)


if __name__ == "__main__":
    main()
