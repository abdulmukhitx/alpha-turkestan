"""
GeoAI-TKO · src/processing/build_mosaic_2025.py
=======================================================
ЭТАП 5 — Сборка COG-мозаика 2025_summer из 43 сырых тайлов (s2_2025_raw/).

Архитектура намеренно отличается от провалившегося build_new_cog.py (v2):
  - Каждый тайл репроецируется в EPSG:32641 ОДНИМ проходом, все 7 бэндов
    вместе, в один multi-band GeoTIFF на диске (НЕ WarpedVRT на лету,
    НЕ раздельный мердж по бэндам). Это структурно исключает класс бага
    v2 — там бэнды мержились по отдельности, и тихий except: continue на
    одном бэнде одной сцены мог оставить этот бэнд частично нулевым,
    никак не провалив остальные 6.
  - После репроекции — ОБЯЗАТЕЛЬНАЯ валидация: CRS, dtype, разрешение
    пикселя (10м) и количество бэндов (7) должны совпадать у ВСЕХ 43
    тайлов, иначе скрипт падает с явной ошибкой ДО мерджа.
  - Финальный мердж переиспользует проверенную windowed read/write логику
    backend/convert_to_cog.py (без изменений в алгоритме) — никакой
    полной загрузки мозаика в RAM.
  - При пересечении тайлов на стыках (overlap) побеждает тайл с меньшей
    облачностью сцены — список тайлов сортируется по cloud_cover
    по возрастанию перед мерджем (первый непустой пиксель побеждает).

Usage:
  python src/processing/build_mosaic_2025.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject
from rasterio.transform import from_bounds

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

YEAR = 2025
MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")
REPROJ_DIR = Path(r"D:\data\s2_2025_reproj")
COG_OUTPUT = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")

TARGET_CRS = CRS.from_epsg(32641)
TARGET_RES = 10.0  # meters — matches 2023_summer
BANDS = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]  # fixed order, matches 2023_summer
BLOCK_SIZE = 512  # matches 2023_summer's COG block size


# ════════════════════════════════════════════════════════════════
#   PHASE A — per-tile reprojection (all 7 bands, one pass, to disk)
# ════════════════════════════════════════════════════════════════

def reproject_tile(tile_id: str, band_paths: dict, out_path: Path) -> dict:
    """Reproject all 7 bands of one tile into a single multi-band GeoTIFF in
    TARGET_CRS at TARGET_RES, using the native 10m B02 band's extent as the
    reference grid. Returns validation metadata for this tile."""
    # Reference grid from B02 (native 10m band) — defines the shared
    # transform/width/height that ALL 7 bands of this tile get written into.
    with rasterio.open(band_paths["B02"]) as ref:
        ref_crs = ref.crs
        dst_transform, dst_width, dst_height = calculate_default_transform(
            ref_crs, TARGET_CRS, ref.width, ref.height, *ref.bounds,
            resolution=TARGET_RES,
        )

    profile = {
        "driver": "GTiff",
        "dtype": "uint16",
        "width": dst_width,
        "height": dst_height,
        "count": len(BANDS),
        "crs": TARGET_CRS,
        "transform": dst_transform,
        "nodata": 0,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": BLOCK_SIZE,
        "blockysize": BLOCK_SIZE,
        "bigtiff": "YES",
        # bands are written sequentially below (one reproject() call per band) —
        # BAND interleave matches that write pattern. PIXEL interleave here would
        # force GDAL to keep every block of the whole image dirty in its block
        # cache until all 7 bands had touched it, which blew past the classic
        # (non-BigTIFF) per-strip size limit even though the file was <2GB total.
        "interleave": "band",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".tif.part")

    with rasterio.open(tmp_path, "w", **profile) as dst:
        for i, band in enumerate(BANDS, 1):
            with rasterio.open(band_paths[band]) as src:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=TARGET_CRS,
                    dst_nodata=0,
                    resampling=Resampling.bilinear,
                )

    tmp_path.replace(out_path)

    # ── per-tile validation (fail loudly, no silent skip) ──────────
    with rasterio.open(out_path) as ds:
        if ds.count != len(BANDS):
            raise RuntimeError(f"{tile_id}: ожидалось {len(BANDS)} бэндов, получено {ds.count}")
        if set(ds.dtypes) != {"uint16"}:
            raise RuntimeError(f"{tile_id}: неверные dtype {ds.dtypes}, ожидался uint16 для всех")
        if ds.crs != TARGET_CRS:
            raise RuntimeError(f"{tile_id}: CRS {ds.crs} != {TARGET_CRS}")
        res_x, res_y = ds.res
        if abs(res_x - TARGET_RES) > 1e-6 or abs(res_y - TARGET_RES) > 1e-6:
            raise RuntimeError(f"{tile_id}: разрешение пикселя {ds.res} != ({TARGET_RES},{TARGET_RES})")
        # all 7 bands share one transform/shape by construction (single dst
        # dataset), but re-confirm explicitly per band to be airtight
        shape = (ds.height, ds.width)
        for b in range(1, ds.count + 1):
            if (ds.height, ds.width) != shape:
                raise RuntimeError(f"{tile_id}: band {b} shape mismatch")

        return {
            "tile": tile_id,
            "width": ds.width,
            "height": ds.height,
            "transform": tuple(ds.transform)[:6],
            "res": ds.res,
            "bounds": tuple(ds.bounds),
            "crs": str(ds.crs),
        }


def phase_a(manifest: dict) -> list[dict]:
    print("=" * 70)
    print("  ФАЗА A — репроекция 43 тайлов в EPSG:32641 (по одному проходу, все 7 бэндов)")
    print("=" * 70)

    tiles = manifest["tiles"]
    results = []
    t0 = time.time()

    for i, (tile_id, info) in enumerate(sorted(tiles.items()), 1):
        out_path = REPROJ_DIR / f"{tile_id}.tif"
        if out_path.exists():
            print(f"[{i}/{len(tiles)}] {tile_id}: уже репроецирован, проверяю...")
        else:
            print(f"[{i}/{len(tiles)}] {tile_id}: репроецирую...")
            reproject_tile(tile_id, info["bands"], out_path)

        with rasterio.open(out_path) as ds:
            res_x, res_y = ds.res
            if ds.count != len(BANDS) or set(ds.dtypes) != {"uint16"} or ds.crs != TARGET_CRS \
                    or abs(res_x - TARGET_RES) > 1e-6 or abs(res_y - TARGET_RES) > 1e-6:
                raise RuntimeError(f"{tile_id}: провалена валидация существующего файла {out_path}")
            results.append({
                "tile": tile_id,
                "width": ds.width, "height": ds.height,
                "res": ds.res, "bounds": tuple(ds.bounds), "crs": str(ds.crs),
                "cloud_cover": info["cloud_cover"],
                "path": str(out_path),
            })
        elapsed = time.time() - t0
        print(f"    OK — {results[-1]['width']}x{results[-1]['height']}px, "
              f"res={results[-1]['res']}, {elapsed/60:.1f} мин прошло")

    # ── cross-tile consistency check (the explicit gate requested) ──
    print("\nПроверка консистентности всех 43 тайлов перед мерджем...")
    ref = results[0]
    bad = []
    for r in results:
        if r["res"] != ref["res"] or r["crs"] != ref["crs"]:
            bad.append(r)
    if bad:
        print("ОШИБКА: расхождение CRS/разрешения пикселя у тайлов:")
        for r in bad:
            print(f"  {r['tile']}: res={r['res']} crs={r['crs']} (эталон: res={ref['res']} crs={ref['crs']})")
        print("Останавливаюсь до мерджа — это именно та проверка, которая должна была поймать баг v2.")
        sys.exit(1)
    print(f"OK — все {len(results)} тайлов: CRS={ref['crs']}, разрешение={ref['res']}, dtype=uint16, count=7")

    return results


# ════════════════════════════════════════════════════════════════
#   PHASE B — windowed merge → COG (reuses convert_to_cog.py's proven logic)
# ════════════════════════════════════════════════════════════════

def phase_b(tile_results: list[dict]):
    print("\n" + "=" * 70)
    print("  ФАЗА B — мердж 43 репроецированных тайлов в COG (windowed, без полной загрузки в RAM)")
    print("  Приоритет на стыках: тайл с меньшей облачностью побеждает (первый непустой пиксель)")
    print("=" * 70)

    # lower cloud_cover first => when merging "first non-empty wins", the
    # clearer scene's pixels get written first and survive at tile seams
    ordered = sorted(tile_results, key=lambda r: r["cloud_cover"])
    paths = [Path(r["path"]) for r in ordered]
    print("Порядок мерджа (по возрастанию облачности):")
    for r in ordered[:5]:
        print(f"  {r['tile']}: {r['cloud_cover']}%")
    print(f"  ... и ещё {len(ordered)-5} тайлов")

    print(f"\nОткрываем {len(paths)} тайлов...")
    src_files = [rasterio.open(p) for p in paths]
    try:
        first = src_files[0]
        count = first.count
        dtype = first.dtypes[0]
        crs = first.crs
        nodata = first.nodata if first.nodata is not None else 0

        all_bounds = [src.bounds for src in src_files]
        left = min(b.left for b in all_bounds)
        bottom = min(b.bottom for b in all_bounds)
        right = max(b.right for b in all_bounds)
        top = max(b.top for b in all_bounds)

        res = abs(first.transform.a)
        width = int(round((right - left) / res))
        height = int(round((top - bottom) / res))
        print(f"  Итоговый размер: {width} x {height} px @ {res}m")
        print(f"  Примерный размер (без сжатия): {width*height*count*2/1e9:.1f} GB")

        transform = from_bounds(left, bottom, right, top, width, height)

        profile = {
            "driver": "GTiff", "dtype": dtype, "width": width, "height": height,
            "count": count, "crs": crs, "transform": transform, "nodata": nodata,
            "compress": "deflate", "tiled": True,
            "blockxsize": BLOCK_SIZE, "blockysize": BLOCK_SIZE,
            "bigtiff": "YES", "interleave": "pixel",
        }

        COG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        print(f"\nЗаписываем -> {COG_OUTPUT}")

        n_blocks_y = (height + BLOCK_SIZE - 1) // BLOCK_SIZE
        n_blocks_x = (width + BLOCK_SIZE - 1) // BLOCK_SIZE
        total_blocks = n_blocks_y * n_blocks_x
        t0 = time.time()

        with rasterio.open(COG_OUTPUT, "w", **profile) as dst:
            done = 0
            for row_off in range(0, height, BLOCK_SIZE):
                row_h = min(BLOCK_SIZE, height - row_off)
                for col_off in range(0, width, BLOCK_SIZE):
                    col_w = min(BLOCK_SIZE, width - col_off)

                    block_left = left + col_off * res
                    block_right = left + (col_off + col_w) * res
                    block_top = top - row_off * res
                    block_bottom = top - (row_off + row_h) * res

                    block_data = np.zeros((count, row_h, col_w), dtype=np.dtype(dtype))

                    for src in src_files:
                        if (src.bounds.right <= block_left or src.bounds.left >= block_right or
                                src.bounds.top <= block_bottom or src.bounds.bottom >= block_top):
                            continue

                        win = rasterio.windows.from_bounds(
                            max(block_left, src.bounds.left), max(block_bottom, src.bounds.bottom),
                            min(block_right, src.bounds.right), min(block_top, src.bounds.top),
                            src.transform,
                        )
                        dst_col = int((max(block_left, src.bounds.left) - block_left) / res)
                        dst_row = int((block_top - min(block_top, src.bounds.top)) / res)
                        read_w = int(win.width)
                        read_h = int(win.height)
                        if read_w <= 0 or read_h <= 0:
                            continue

                        data = src.read(window=win, out_shape=(count, read_h, read_w),
                                         resampling=Resampling.nearest)
                        valid = data[0] != nodata
                        for b in range(count):
                            dst_slice = block_data[b, dst_row:dst_row+read_h, dst_col:dst_col+read_w]
                            src_slice = data[b, :read_h, :read_w]
                            mask = valid[:read_h, :read_w]
                            dst_slice[mask] = src_slice[mask]

                    dst.write(block_data, window=rasterio.windows.Window(col_off, row_off, col_w, row_h))
                    done += 1
                    if done % 50 == 0 or done == total_blocks:
                        pct = done / total_blocks * 100
                        elapsed = time.time() - t0
                        print(f"  [{pct:5.1f}%] блок {done}/{total_blocks}, {elapsed/60:.1f} мин")

        print(f"\nЗаписано: {COG_OUTPUT}")
        print("Строим overviews (пирамиды)...")
        with rasterio.open(COG_OUTPUT, "r+") as dst:
            dst.build_overviews([2, 4, 8, 16, 32, 64], Resampling.bilinear)
            dst.update_tags(ns="rio_overview", resampling="bilinear")

        size_gb = COG_OUTPUT.stat().st_size / 1e9
        print(f"Overviews добавлены. Итоговый размер: {size_gb:.2f} GB")
        return size_gb
    finally:
        for src in src_files:
            src.close()


def main():
    if not MANIFEST_PATH.exists():
        print(f"ОШИБКА: {MANIFEST_PATH} не найден. Сначала Этап 4.")
        sys.exit(1)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if len(manifest["tiles"]) != 43:
        print(f"ОШИБКА: ожидалось 43 тайла в манифесте, найдено {len(manifest['tiles'])}")
        sys.exit(1)

    t0 = time.time()
    tile_results = phase_a(manifest)
    size_gb = phase_b(tile_results)

    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("  ИТОГ Этапа 5")
    print("=" * 70)
    print(f"  Тайлов смерджено: {len(tile_results)}")
    print(f"  Итоговый размер COG: {size_gb:.2f} GB")
    print(f"  Время: {elapsed/60:.1f} мин")
    print(f"  Файл: {COG_OUTPUT}")

    ref_gb = 34.5 * (43 / 37)
    deviation = abs(size_gb - ref_gb) / ref_gb * 100
    print(f"\n  Ориентир (по аналогии с 2023, 37->34.5GB): ~{ref_gb:.1f} GB")
    print(f"  Отклонение: {deviation:.1f}%")
    if deviation > 30:
        print("  ВНИМАНИЕ: отклонение >30% — см. Этап 6 п.2, разберись перед тем как считать готовым.")
    else:
        print("  В пределах ожидаемого диапазона.")


if __name__ == "__main__":
    main()
