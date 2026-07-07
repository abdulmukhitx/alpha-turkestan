"""
GeoAI-TKO · src/processing/verify_overview_2023_cdse.py
=======================================================
ЭТАП 4 (2023_summer CDSE rebuild) — независимая верификация overview-
пирамиды (Урок 6, полный цикл): build_overviews() внутри build_mosaic_2025.
phase_b может завершиться без исключений, но НЕ отразить реальные изменения
в пирамиде, если её строить на том же хендле, что писал полное разрешение.
phase_b уже делает close()+переоткрытие "r+" на свежем хендле перед
build_overviews() — но НЕ делает шаг 5 (независимое чтение отдельным
read-only хендлом для сравнения full-res vs overview). Этот скрипт — тот
недостающий шаг.

Три проверки:
  1. Overview levels присутствуют и корректны (метаданные)
  2. 6 точек по всей площади (full extent, не только внутри границы —
     проверяем именно консистентность overview/full-res, не территорию),
     выровненных на границу блока 64x64 — full-res блок и overview-пиксель
     (factor=64) должны показывать одинаковый nodata-статус и близкие
     значения (overview построен Resampling.nearest, не average)
  3. Для 3 тайлов с n_unique_orbits_in_top3=1 (41TQH, 42TXK, 42TXL,
     см. Этап 1) — реальный full-res nodata% ВНУТРИ официальной границы
     (Урок 10: boundary-restricted, не padded AOI bbox) в финальном
     смердженном файле (не в их собственном промежуточном composite —
     соседние тайлы на стыке могли уже закрыть дыру при мердже)

Usage:
  python src/processing/verify_overview_2023_cdse.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
import rasterio.windows
from pyproj import Transformer
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_mosaic import load_boundary_union  # noqa: E402 — reuse, not reimplement

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

COG_PATH = Path(r"D:\data\mosaics\2023_summer_cdse\s2_mosaic_cog.tif")
REPROJ_DIR = Path(r"D:\data\s2_2023_cdse_reproj")
LOW_ORBIT_TILES = ["41TQH", "42TXK", "42TXL"]
BAND_NAMES = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]
BLOCK = 64  # matches the coarsest overview factor built (2,4,8,16,32,64)
N_BOUNDARY_POINTS_PER_TILE = 300


def check_overview_metadata(ds) -> dict:
    print("=" * 70)
    print("  1/3 — метаданные overview")
    print("=" * 70)
    levels = ds.overviews(1)
    print(f"  Overview levels (band 1): {levels}")
    ok = levels == [2, 4, 8, 16, 32, 64]
    print(f"  {'OK' if ok else 'ВНИМАНИЕ: не совпадает с ожидаемым [2,4,8,16,32,64]'}")
    return {"levels": levels, "ok": ok}


def check_fullres_vs_overview(ds) -> dict:
    print("\n" + "=" * 70)
    print("  2/3 — full-res vs overview (factor=64), 6 точек по площади")
    print("=" * 70)
    H, W = ds.height, ds.width
    file_nodata = ds.nodata if ds.nodata is not None else -9999.0

    # 6 points spread across the raster, snapped to a 64-multiple grid so
    # each full-res 64x64 block maps onto exactly one overview-64 pixel —
    # no ambiguity in which source pixel the decimated read corresponds to.
    fracs = [(0.15, 0.25), (0.5, 0.25), (0.85, 0.25),
             (0.15, 0.75), (0.5, 0.75), (0.85, 0.75)]
    results = []
    for fx, fy in fracs:
        col0 = int((fx * W) // BLOCK) * BLOCK
        row0 = int((fy * H) // BLOCK) * BLOCK
        col0 = min(col0, W - BLOCK)
        row0 = min(row0, H - BLOCK)

        full_block = ds.read(window=rasterio.windows.Window(col0, row0, BLOCK, BLOCK))
        full_nodata_mask = full_block[0] == file_nodata
        full_nodata_pct = round(float(100 * full_nodata_mask.sum() / full_nodata_mask.size), 1)
        valid_vals = {BAND_NAMES[i]: full_block[i][~full_nodata_mask] for i in range(len(BAND_NAMES))}

        # Decimated read at exactly the built factor -> GDAL serves this
        # from the overview level itself rather than re-resampling full-res.
        ov_pixel = ds.read(
            window=rasterio.windows.Window(col0, row0, BLOCK, BLOCK),
            out_shape=(len(BAND_NAMES), 1, 1),
        )[:, 0, 0]
        ov_is_nodata = bool(ov_pixel[0] == file_nodata)

        row = {
            "row0": row0, "col0": col0,
            "full_nodata_pct": full_nodata_pct,
            "overview_is_nodata": ov_is_nodata,
            "consistent": bool((full_nodata_pct == 100.0) == ov_is_nodata),
            "bands": {},
        }
        for i, name in enumerate(BAND_NAMES):
            vv = valid_vals[name]
            if vv.size == 0:
                row["bands"][name] = {"full_range": None, "overview_value": round(float(ov_pixel[i]), 4)}
            else:
                lo, hi = float(vv.min()), float(vv.max())
                ov_v = float(ov_pixel[i])
                in_range = bool((lo - 1e-4) <= ov_v <= (hi + 1e-4)) if not ov_is_nodata else None
                row["bands"][name] = {
                    "full_range": [round(lo, 4), round(hi, 4)],
                    "overview_value": round(ov_v, 4),
                    "overview_in_full_range": in_range,
                }
        results.append(row)

        print(f"\n  Точка (row0={row0}, col0={col0}):")
        print(f"    full-res 64x64 nodata%: {full_nodata_pct}%  |  overview(factor=64) nodata: {ov_is_nodata}")
        print(f"    {'СОГЛАСОВАНО' if row['consistent'] else 'ВНИМАНИЕ: РАСХОЖДЕНИЕ nodata-статуса'}")
        for name in ["B04", "B08"]:
            b = row["bands"][name]
            if b["full_range"] is None:
                print(f"    {name}: full-res всё nodata, overview={b['overview_value']}")
            else:
                flag = "" if b.get("overview_in_full_range") else "  <-- ВНЕ диапазона full-res!"
                print(f"    {name}: full-res диапазон={b['full_range']}, overview={b['overview_value']}{flag}")

    all_consistent = all(r["consistent"] for r in results)
    print(f"\n  Итог: {sum(r['consistent'] for r in results)}/{len(results)} точек согласованы по nodata-статусу")
    print(f"  {'OK — overview актуален' if all_consistent else 'ОШИБКА — overview расходится с full-res, требуется пересборка'}")
    return {"points": results, "all_consistent": all_consistent}


def check_low_orbit_tiles(ds, boundary_union) -> dict:
    print("\n" + "=" * 70)
    print("  3/3 — три тайла с n_unique_orbits_in_top3=1 (41TQH, 42TXK, 42TXL)")
    print("  Реальный full-res nodata% ВНУТРИ официальной границы, в финальном мердже")
    print("=" * 70)
    file_nodata = ds.nodata if ds.nodata is not None else -9999.0
    to_wgs84 = Transformer.from_crs(ds.crs, "EPSG:4326", always_xy=True)
    results = {}

    for tile in LOW_ORBIT_TILES:
        tile_path = REPROJ_DIR / f"{tile}.tif"
        if not tile_path.exists():
            print(f"\n  {tile}: ОШИБКА — {tile_path} не найден")
            results[tile] = {"error": "reproj file missing"}
            continue
        with rasterio.open(tile_path) as tds:
            bounds = tds.bounds

        win = rasterio.windows.from_bounds(*bounds, transform=ds.transform)
        col0, row0 = int(win.col_off), int(win.row_off)
        w, h = int(round(win.width)), int(round(win.height))
        col0 = max(0, col0)
        row0 = max(0, row0)
        w = min(w, ds.width - col0)
        h = min(h, ds.height - row0)

        # raw nodata% over the tile's full bbox window (includes any margin
        # beyond real coverage, informational)
        band0 = ds.read(1, window=rasterio.windows.Window(col0, row0, w, h))
        raw_nodata_pct = round(float(100 * (band0 == file_nodata).sum() / band0.size), 2)

        # boundary-restricted: random points inside the tile's projected bbox,
        # transformed to WGS84, kept only if inside the official boundary,
        # then sampled directly from the merged COG (Урок 10)
        rng_cols = np.random.default_rng(42).uniform(col0, col0 + w, N_BOUNDARY_POINTS_PER_TILE * 3)
        rng_rows = np.random.default_rng(43).uniform(row0, row0 + h, N_BOUNDARY_POINTS_PER_TILE * 3)
        n_inside = 0
        n_inside_nodata = 0
        for c, r in zip(rng_cols, rng_rows):
            if n_inside >= N_BOUNDARY_POINTS_PER_TILE:
                break
            x, y = ds.xy(int(r), int(c))
            lon, lat = to_wgs84.transform(x, y)
            if not boundary_union.contains(Point(lon, lat)):
                continue
            n_inside += 1
            val = ds.read(1, window=rasterio.windows.Window(int(c), int(r), 1, 1))[0, 0]
            if val == file_nodata:
                n_inside_nodata += 1

        boundary_nodata_pct = round(100 * n_inside_nodata / n_inside, 2) if n_inside else None
        results[tile] = {
            "raw_bbox_nodata_pct": raw_nodata_pct,
            "boundary_points_checked": n_inside,
            "boundary_nodata_count": n_inside_nodata,
            "boundary_nodata_pct": boundary_nodata_pct,
        }
        print(f"\n  {tile}:")
        print(f"    nodata% по всему bbox тайла (вкл. буфер): {raw_nodata_pct}%")
        print(f"    nodata% СТРОГО внутри официальной границы: {boundary_nodata_pct}% "
              f"({n_inside_nodata}/{n_inside} точек)")
        if boundary_nodata_pct and boundary_nodata_pct > 5:
            print(f"    <-- ВНИМАНИЕ: >5% honest nodata внутри границы, возможно нужен orbit-патч")
        else:
            print(f"    OK — дыр внутри официальной территории практически нет")

    return results


def main():
    if not COG_PATH.exists():
        print(f"ОШИБКА: {COG_PATH} не найден.")
        sys.exit(1)

    boundary_union = load_boundary_union()

    # Independent read-only handle — NOT the process that wrote the file
    # (that process already exited after Этап 3).
    with rasterio.open(COG_PATH, "r") as ds:
        meta = check_overview_metadata(ds)
        fullres_vs_ov = check_fullres_vs_overview(ds)
        low_orbit = check_low_orbit_tiles(ds, boundary_union)

    print("\n" + "=" * 70)
    print("  ИТОГ Этапа 4")
    print("=" * 70)
    print(f"  Overview levels корректны: {meta['ok']}")
    print(f"  Full-res/overview консистентны: {fullres_vs_ov['all_consistent']}")
    for tile, r in low_orbit.items():
        if "error" in r:
            print(f"  {tile}: ОШИБКА чтения")
        else:
            print(f"  {tile}: boundary nodata% = {r['boundary_nodata_pct']}%")

    out = {
        "overview_metadata": meta,
        "fullres_vs_overview": fullres_vs_ov,
        "low_orbit_tiles": low_orbit,
    }
    out_path = Path(r"D:\data\mosaics\2023_summer_cdse\overview_verification.json")
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Сохранено: {out_path}")


if __name__ == "__main__":
    main()
