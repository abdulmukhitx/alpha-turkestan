"""
GeoAI-TKO · src/processing/check_data_availability.py
=======================================================
Read-only survey of Sentinel-2 L2A summer (Jun-Aug) coverage for the AOI,
year by year, via the CDSE STAC API. Does NOT download any imagery — this
is purely to decide which years are worth adding to the mosaic archive.

For each year it fetches every L2A product over the AOI for Jun1-Aug31
(no cloud filter, so we can also see 40-60% fallback candidates), groups
by MGRS tile, and reports how many of the tiles needed to cover the AOI
have at least one usable (<=40% cloud) scene that year.

Usage:
  python src/processing/check_data_availability.py
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
COLLECTION = "sentinel-2-l2a"
CLOUD_COVER_MAX = 40
FALLBACK_CLOUD_MAX = 60
YEARS = list(range(2017, 2027))
OUT_PATH = Path(r"D:\data\availability_report.json")

# AOI with margin — exact oblast boundary clip happens later, this is just for the survey
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

REQUIRED_BANDS = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]


def fetch_summer(year: int) -> list[dict]:
    """All L2A products over the AOI for Jun1-Aug31 of `year`, any cloud cover."""
    body = {
        "collections": [COLLECTION],
        "intersects": AOI,
        "datetime": f"{year}-06-01T00:00:00Z/{year}-08-31T23:59:59Z",
        "limit": 200,
    }
    items = []
    url = STAC_URL
    while True:
        r = requests.post(url, json=body, timeout=60)
        r.raise_for_status()
        d = r.json()
        for f in d.get("features", []):
            p = f["properties"]
            tile = (p.get("grid:code") or "").replace("MGRS-", "")
            cc = p.get("eo:cloud_cover")
            dt = p.get("datetime")
            if tile and cc is not None:
                items.append({"tile": tile, "cloud_cover": cc, "datetime": dt})
        nxt = next((l for l in d.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt["href"]
        body = nxt.get("body", body)
    return items


def main():
    print("=" * 70)
    print("  GeoAI-TKO: Sentinel-2 L2A data availability survey (READ-ONLY)")
    print(f"  Collection: {COLLECTION}  |  cloud_cover <= {CLOUD_COVER_MAX}%")
    print(f"  Season: Jun 1 - Aug 31, each year {YEARS[0]}-{YEARS[-1]}")
    print("=" * 70)

    today = datetime.now(timezone.utc).date()

    per_year_raw = {}
    for year in YEARS:
        items = fetch_summer(year)
        per_year_raw[year] = items
        print(f"  {year}: {len(items)} products found")

    # MGRS tile grid is fixed over time — union across all years gives the
    # canonical set of tiles needed to cover this AOI.
    master_tiles = sorted({it["tile"] for items in per_year_raw.values() for it in items})
    n_required = len(master_tiles)
    print(f"\nMaster tile set for this AOI: {n_required} tiles")
    print(f"  {master_tiles}")

    summary = []
    detail = {}
    for year in YEARS:
        items = per_year_raw[year]
        by_tile = defaultdict(list)
        for it in items:
            by_tile[it["tile"]].append(it["cloud_cover"])

        best_per_tile = {t: (min(by_tile[t]) if by_tile.get(t) else None) for t in master_tiles}
        covered = [t for t, c in best_per_tile.items() if c is not None and c <= CLOUD_COVER_MAX]
        missing = [t for t in master_tiles if t not in covered]

        coverage_pct = round(len(covered) / n_required * 100, 1) if n_required else 0.0
        worst_cloud = round(max(best_per_tile[t] for t in covered), 1) if covered else None

        fallback = {
            t: round(best_per_tile[t], 1)
            for t in missing
            if best_per_tile[t] is not None and best_per_tile[t] <= FALLBACK_CLOUD_MAX
        }
        still_missing = [t for t in missing if t not in fallback]

        season_incomplete = (year == today.year and today.month <= 8)

        summary.append({
            "year": year,
            "found_tiles": len(covered),
            "required_tiles": n_required,
            "coverage_pct": coverage_pct,
            "worst_cloud_cover": worst_cloud,
            "missing_tiles": missing,
            "fallback_candidates_40_60": fallback,
            "still_missing_no_fallback": still_missing,
            "season_incomplete": season_incomplete,
        })
        detail[str(year)] = {
            "best_cloud_per_tile": best_per_tile,
            "raw_product_count": len(items),
        }

    # ── print summary table ──────────────────────────────────────────
    print("\nГод  | Найдено тайлов | Нужно тайлов | Покрытие | Худший случай облачности")
    print("-" * 78)
    for row in summary:
        worst = f"{row['worst_cloud_cover']:.1f}%" if row["worst_cloud_cover"] is not None else "-"
        note = "  (сезон ещё не завершён)" if row["season_incomplete"] else ""
        print(f"{row['year']} |       {row['found_tiles']:>3}       |      {row['required_tiles']:>3}      "
              f"|  {row['coverage_pct']:>5.1f}%  |  {worst}{note}")

    # ── gaps + fallback candidates ───────────────────────────────────
    print("\n" + "=" * 70)
    print("  Годы с неполным покрытием (<100%)")
    print("=" * 70)
    any_gaps = False
    for row in summary:
        if row["coverage_pct"] >= 100.0:
            continue
        any_gaps = True
        print(f"\n{row['year']}: покрытие {row['coverage_pct']}% ({row['found_tiles']}/{row['required_tiles']})")
        if row["fallback_candidates_40_60"]:
            print("  Можно закрыть снимками с облачностью 40-60%:")
            for t, c in row["fallback_candidates_40_60"].items():
                print(f"    {t}: {c}%")
        if row["still_missing_no_fallback"]:
            print(f"  Нет данных вообще (даже >60% облачности) для: {row['still_missing_no_fallback']}")
    if not any_gaps:
        print("  Нет — все года имеют 100% покрытие.")

    # ── save full report ─────────────────────────────────────────────
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": AOI,
        "collection": COLLECTION,
        "required_bands": REQUIRED_BANDS,
        "cloud_cover_max": CLOUD_COVER_MAX,
        "fallback_cloud_cover_max": FALLBACK_CLOUD_MAX,
        "master_tiles": master_tiles,
        "summary": summary,
        "detail": detail,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nПолный отчёт сохранён: {OUT_PATH}")


if __name__ == "__main__":
    main()
