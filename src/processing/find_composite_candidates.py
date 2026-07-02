"""
GeoAI-TKO - src/processing/find_composite_candidates.py
=======================================================
ЗАДАЧА 2, Шаг 2.1 - для каждого из 43 master tile_id запросить ВСЕ продукты
за лето 2025 (2025-06-01 - 2025-08-31, cloud_cover < 40%) и взять топ-3
(primary/secondary/tertiary) для temporal compositing (закрытие дырок от
границы орбиты одиночного продукта).

ВАЖНО (пост-мортем 2026-07-01): чистый top-3-by-cloud без учёта orbit
регулярно брал все 3 продукта с ОДНОГО relative orbit — если тайл лежит на
стыке двух орбитальных проходов, вся половина тайла, которую покрывает
только другой orbit, оставалась честным nodata даже после compositing
(обнаружено на 7/43 тайлов, remaining_nodata_pct от 33% до 98%). Диагностика
и точечные патчи для уже собранного 2025_summer — в
D:\\data\\gap_diagnosis.json и src/processing/download_orbit_patch.py.

Поэтому отбор теперь ЖАДНО гарантирует orbit-разнообразие: кандидат #1 —
глобально лучший по облачности; каждый следующий — лучший по облачности
СРЕДИ ЕЩЁ НЕ ПРЕДСТАВЛЕННЫХ orbit (если такие есть), иначе — лучший из
оставшихся вообще. Это не гарантирует полное геометрическое покрытие (для
этого см. check_tile_orbit_coverage.py — регрессионный тест на STAC
geometry), но устраняет главную причину: слепой выбор из одного orbit.

Не скачивает ничего — только строит и сохраняет план кандидатов
(D:\\data\\s2_2025_raw\\composite_candidates.json), чтобы оценить сколько
реально доступно ПЕРЕД тем как качать secondary/tertiary assets.
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
TOP_N = 3

REPORT_PATH = Path(r"D:\data\availability_report.json")
MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")
OUT_PATH = Path(r"D:\data\s2_2025_raw\composite_candidates.json")


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
            "feature": f,
        })
    for tile in by_tile:
        by_tile[tile].sort(key=lambda x: x["cloud_cover"])
    return by_tile


def select_orbit_diverse_top_n(candidates: list[dict], n: int) -> list[dict]:
    """Greedy selection: candidate #1 is the global best-by-cloud. Each next
    slot prefers the best-by-cloud candidate from an orbit NOT YET selected;
    if every remaining orbit is already represented, falls back to the next
    best-by-cloud overall. `candidates` must already be sorted by cloud_cover
    ascending."""
    if not candidates:
        return []
    selected = [candidates[0]]
    used_orbits = {candidates[0]["relative_orbit"]}
    remaining = candidates[1:]

    while len(selected) < n and remaining:
        pick_idx = None
        for i, c in enumerate(remaining):
            if c["relative_orbit"] not in used_orbits:
                pick_idx = i
                break
        if pick_idx is None:
            pick_idx = 0  # no unrepresented orbit left — take next best overall
        picked = remaining.pop(pick_idx)
        selected.append(picked)
        used_orbits.add(picked["relative_orbit"])

    return selected


def main():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    master_tiles = report["master_tiles"]
    aoi = report["aoi"]
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    print("=" * 70)
    print(f"  Задача 2, Шаг 2.1 — поиск top-{TOP_N} кандидатов на {len(master_tiles)} тайлов")
    print("=" * 70)
    print("\nЗапрашиваю STAC (все продукты лета 2025, без ограничения по best-per-tile)...")
    feats = fetch_summer(YEAR, aoi)
    print(f"Всего продуктов найдено: {len(feats)}")

    by_tile = group_by_tile(master_tiles, feats)

    result = {"generated_at": None, "tiles": {}}
    from datetime import datetime, timezone
    result["generated_at"] = datetime.now(timezone.utc).isoformat()

    print(f"\n{'Тайл':>8} {'Всего':>6} {'Primary CC':>11} {'Secondary CC':>13} {'Tertiary CC':>12}")
    stats_1 = stats_2 = stats_3 = 0
    for tile in sorted(master_tiles):
        candidates = by_tile.get(tile, [])
        top = select_orbit_diverse_top_n(candidates, TOP_N)
        primary_used = manifest["tiles"].get(tile, {}).get("product_id")

        new_primary_id = top[0]["product_id"] if top else None
        primary_changed = bool(primary_used and new_primary_id and primary_used != new_primary_id)

        n_unique_orbits = len({c["relative_orbit"] for c in top})
        entry = {
            "n_total_candidates": len(candidates),
            "candidates": [
                {"product_id": c["product_id"], "cloud_cover": c["cloud_cover"],
                 "datetime": c["datetime"], "relative_orbit": c["relative_orbit"]}
                for c in top
            ],
            "n_unique_orbits_in_top3": n_unique_orbits,
            "primary_changed_from_original": primary_changed,
            "original_manifest_product_id": primary_used,
            "new_primary_product_id": new_primary_id,
        }
        result["tiles"][tile] = entry

        cc1 = top[0]["cloud_cover"] if len(top) > 0 else None
        cc2 = top[1]["cloud_cover"] if len(top) > 1 else None
        cc3 = top[2]["cloud_cover"] if len(top) > 2 else None
        if cc1 is not None:
            stats_1 += 1
        if cc2 is not None:
            stats_2 += 1
        if cc3 is not None:
            stats_3 += 1

        # sanity: is the manifest's chosen product actually the primary here?
        mismatch = ""
        if top and primary_used and top[0]["product_id"] != primary_used:
            mismatch = "  <-- ВНИМАНИЕ: primary != manifest.json product_id"

        print(f"{tile:>8} {len(candidates):>6} {str(cc1):>11} {str(cc2):>13} {str(cc3):>12}{mismatch}")

    print("\n" + "=" * 70)
    print("  ИТОГ Шага 2.1")
    print("=" * 70)
    print(f"  Тайлов с >=1 кандидатом (primary):   {stats_1}/{len(master_tiles)}")
    print(f"  Тайлов с >=2 кандидатами (secondary): {stats_2}/{len(master_tiles)}")
    print(f"  Тайлов с >=3 кандидатами (tertiary):  {stats_3}/{len(master_tiles)}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Сохранено: {OUT_PATH}")


if __name__ == "__main__":
    main()
