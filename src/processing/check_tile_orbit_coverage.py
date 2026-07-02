"""
GeoAI-TKO - src/processing/check_tile_orbit_coverage.py
=======================================================
Регрессионный инструмент проверки геометрического покрытия: для набора
кандидатов тайла (обычно top-3 из composite_candidates.json) проверяет,
насколько объединение их РЕАЛЬНЫХ footprint-геометрий (STAC item geometry,
не bbox) покрывает bounding box тайла — без скачивания JP2, только
метаданные STAC.

Изначально написан как разовая диагностика для 42TVN/42TUL (orbit-seam
gap), затем расширен и повторно использован для 5 других тайлов с той же
проблемой. Теперь — постоянный инструмент: запускать после ЛЮБОГО
изменения логики отбора кандидатов (find_composite_candidates.py), чтобы
подтвердить, что отбор реально даёт хорошее покрытие, а не просто
"звучит правильно" (добавили orbit-разнообразие — не значит само собой
что geometry сходится).

Usage:
  # проверить все тайлы из уже сохранённого composite_candidates.json
  python src/processing/check_tile_orbit_coverage.py

  # проверить конкретные тайлы
  python src/processing/check_tile_orbit_coverage.py --tiles 42TVN 42TUL
"""
import argparse
import json
import sys
from pathlib import Path

import rasterio
import requests
from rasterio.warp import transform_bounds
from shapely.geometry import shape, box
from shapely.ops import unary_union

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
COLLECTION = "sentinel-2-l2a"

MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")
CANDIDATES_PATH = Path(r"D:\data\s2_2025_raw\composite_candidates.json")

COVERAGE_WARN_THRESHOLD = 90.0  # % of tile bbox that must be covered by union of candidate footprints


def tile_bbox_4326(tile: str, manifest: dict) -> box:
    b02_path = manifest["tiles"][tile]["bands"]["B02"]
    with rasterio.open(b02_path) as ds:
        return box(*transform_bounds(ds.crs, "EPSG:4326", *ds.bounds))


def fetch_item_geometry(product_id: str):
    body = {"collections": [COLLECTION], "ids": [product_id], "limit": 1}
    r = requests.post(STAC_URL, json=body, timeout=30)
    r.raise_for_status()
    feats = r.json().get("features", [])
    if not feats:
        return None
    return shape(feats[0]["geometry"])


def check_tile(tile: str, candidates: list[dict], manifest: dict) -> dict:
    bbox = tile_bbox_4326(tile, manifest)
    geoms = []
    orbits = set()
    for c in candidates:
        geom = fetch_item_geometry(c["product_id"])
        if geom is not None:
            geoms.append(geom)
        orbits.add(c.get("relative_orbit", "?"))

    if not geoms:
        return {"tile": tile, "coverage_pct": 0.0, "n_orbits": len(orbits), "orbits": sorted(orbits), "status": "NO_GEOMETRY"}

    union = unary_union(geoms)
    inter = union.intersection(bbox)
    coverage_pct = 100 * inter.area / bbox.area if bbox.area > 0 else 0

    status = "OK" if coverage_pct >= COVERAGE_WARN_THRESHOLD else "LOW_COVERAGE"
    return {"tile": tile, "coverage_pct": round(coverage_pct, 1), "n_orbits": len(orbits),
            "orbits": sorted(orbits), "status": status}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", nargs="*", default=None, help="конкретные тайлы (по умолчанию — все из composite_candidates.json)")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    candidates_data = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))["tiles"]

    tiles = args.tiles if args.tiles else sorted(candidates_data.keys())

    print("=" * 70)
    print(f"  Регрессионная проверка geometric coverage — {len(tiles)} тайлов")
    print("=" * 70)
    print(f"{'Тайл':>8} {'coverage%':>10} {'orbits':>8} {'детали':>20} {'статус':>14}")

    results = []
    low_coverage_tiles = []
    for tile in tiles:
        candidates = candidates_data[tile]["candidates"]
        r = check_tile(tile, candidates, manifest)
        results.append(r)
        marker = "  <-- ВНИМАНИЕ" if r["status"] != "OK" else ""
        print(f"{tile:>8} {r['coverage_pct']:>9.1f}% {r['n_orbits']:>8} {str(r['orbits']):>20} {r['status']:>14}{marker}")
        if r["status"] != "OK":
            low_coverage_tiles.append(r)

    print("\n" + "=" * 70)
    print("  ИТОГ")
    print("=" * 70)
    print(f"  Проверено: {len(results)} тайлов")
    print(f"  OK (coverage >= {COVERAGE_WARN_THRESHOLD}%): {len(results) - len(low_coverage_tiles)}")
    print(f"  LOW_COVERAGE: {len(low_coverage_tiles)}")
    if low_coverage_tiles:
        print("\n  Тайлы, требующие внимания:")
        for r in low_coverage_tiles:
            print(f"    {r['tile']}: {r['coverage_pct']}% ({r['n_orbits']} orbit(s): {r['orbits']})")

    out_path = Path(r"D:\data\orbit_coverage_check.json")
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Сохранено: {out_path}")


if __name__ == "__main__":
    main()
