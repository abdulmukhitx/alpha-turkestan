"""
GeoAI-TKO · src/processing/estimate_download_size.py
=======================================================
ЭТАП 3 — Оценка объёма скачивания для 2025_summer. READ-ONLY: только
STAC-запросы и суммирование file:size из метаданных, ничего не скачивает.

Логика:
  1. Берём 43 master tile из availability_report.json (2017-2026 survey).
  2. Для каждого тайла находим лучший (минимальная облачность) продукт
     Sentinel-2 L2A за лето 2025 через CDSE STAC.
  3. Суммируем file:size только нужных ассетов: B02_10m, B03_10m, B04_10m,
     B08_10m (10м бэнды) + B05_20m, B8A_20m, B11_20m (20м бэнды, как в
     родном продукте — апсемплинг до 10м будет на этапе репроекции).
  4. Экстраполируем итоговый размер COG по аналогии с 2023 (37 тайлов → 34.5GB).

Usage:
  python src/processing/estimate_download_size.py
"""
import json
import sys
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
COLLECTION = "sentinel-2-l2a"
CLOUD_COVER_MAX = 40
YEAR = 2025
REPORT_PATH = Path(r"D:\data\availability_report.json")
OUT_PATH = Path(r"D:\data\download_estimate_2025.json")

NEEDED_ASSETS = ["B02_10m", "B03_10m", "B04_10m", "B08_10m", "B05_20m", "B8A_20m", "B11_20m"]

# Reference point from the working 2023_summer build
REF_TILE_COUNT = 37
REF_COG_SIZE_GB = 34.5

AOI = {
    "type": "Polygon",
    "coordinates": [[
        [65.928955, 40.530502],
        [70.97168, 40.530502],
        [70.97168, 46.035109],
        [65.928955, 46.035109],
        [65.928955, 40.530502],
    ]],
}


def fetch_summer(year: int) -> list[dict]:
    body = {
        "collections": [COLLECTION],
        "intersects": AOI,
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


def main():
    if not REPORT_PATH.exists():
        print(f"ОШИБКА: {REPORT_PATH} не найден. Сначала запусти check_data_availability.py")
        sys.exit(1)

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    master_tiles = set(report["master_tiles"])
    print(f"Master tile set: {len(master_tiles)} тайлов (из availability_report.json)")

    print(f"\nЗапрашиваю STAC за лето {YEAR} (cloud_cover < {CLOUD_COVER_MAX}%)...")
    feats = fetch_summer(YEAR)
    print(f"  Найдено продуктов: {len(feats)}")

    # best (lowest cloud) product per tile
    best_per_tile = {}
    for f in feats:
        p = f["properties"]
        tile = (p.get("grid:code") or "").replace("MGRS-", "")
        if tile not in master_tiles:
            continue
        cc = p.get("eo:cloud_cover")
        if cc is None:
            continue
        cur = best_per_tile.get(tile)
        if cur is None or cc < cur["cloud_cover"]:
            best_per_tile[tile] = {"feature": f, "cloud_cover": cc}

    missing_tiles = sorted(master_tiles - set(best_per_tile.keys()))
    if missing_tiles:
        print(f"\n⚠ ВНИМАНИЕ: для {len(missing_tiles)} тайлов нет продукта <{CLOUD_COVER_MAX}% за {YEAR}: {missing_tiles}")
        print("  Это расходится с availability_report.json — остановись и разберись перед Этапом 4.")

    print(f"\nНайдены лучшие продукты для {len(best_per_tile)}/{len(master_tiles)} тайлов. Считаю размеры ассетов...")

    per_tile_info = []
    total_bytes = 0
    missing_assets_total = []
    for tile in sorted(best_per_tile.keys()):
        info = best_per_tile[tile]
        f = info["feature"]
        assets = f.get("assets", {})
        tile_bytes = 0
        missing_assets = []
        for key in NEEDED_ASSETS:
            a = assets.get(key)
            size = a.get("file:size") if a else None
            if size is None:
                missing_assets.append(key)
                continue
            tile_bytes += size
        total_bytes += tile_bytes
        if missing_assets:
            missing_assets_total.append((tile, missing_assets))
        per_tile_info.append({
            "tile": tile,
            "product_id": f["id"],
            "cloud_cover": round(info["cloud_cover"], 2),
            "bytes": tile_bytes,
            "gb": round(tile_bytes / 1e9, 3),
            "missing_assets": missing_assets,
        })

    total_gb = total_bytes / 1e9
    estimated_cog_gb = REF_COG_SIZE_GB * (len(master_tiles) / REF_TILE_COUNT)

    print("\n" + "=" * 70)
    print("  ИТОГ — Этап 3: оценка объёма скачивания (2025_summer)")
    print("=" * 70)
    print(f"  Тайлов: {len(per_tile_info)}/{len(master_tiles)}")
    print(f"  Нужные ассеты на тайл: {NEEDED_ASSETS}")
    print(f"\n  Ожидаемый объём скачивания (сырые JP2, только нужные бэнды): {total_gb:.2f} GB")
    print(f"  Ожидаемый размер итогового COG (по аналогии с 2023: "
          f"{REF_TILE_COUNT} тайлов → {REF_COG_SIZE_GB} GB): ≈ {estimated_cog_gb:.1f} GB")

    if missing_assets_total:
        print(f"\n⚠ У {len(missing_assets_total)} тайлов отсутствуют некоторые ассеты (нет file:size в STAC):")
        for tile, ma in missing_assets_total:
            print(f"    {tile}: отсутствуют {ma}")

    # per-tile table (top 5 largest, for sanity-check)
    per_tile_info_sorted = sorted(per_tile_info, key=lambda x: -x["bytes"])
    print("\n  5 самых тяжёлых тайлов:")
    for row in per_tile_info_sorted[:5]:
        print(f"    {row['tile']}: {row['gb']:.2f} GB (облачность {row['cloud_cover']}%)")
    print("  5 самых лёгких тайлов:")
    for row in per_tile_info_sorted[-5:]:
        print(f"    {row['tile']}: {row['gb']:.2f} GB (облачность {row['cloud_cover']}%)")

    result = {
        "year": YEAR,
        "master_tile_count": len(master_tiles),
        "tiles_with_product": len(per_tile_info),
        "missing_tiles": missing_tiles,
        "needed_assets": NEEDED_ASSETS,
        "total_download_bytes": total_bytes,
        "total_download_gb": round(total_gb, 3),
        "estimated_cog_gb": round(estimated_cog_gb, 1),
        "reference": {"tile_count": REF_TILE_COUNT, "cog_size_gb": REF_COG_SIZE_GB},
        "per_tile": per_tile_info,
    }
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nПолный результат сохранён: {OUT_PATH}")
    print("\nСкачивание НЕ выполнялось — это только оценка (Этап 3).")


if __name__ == "__main__":
    main()
