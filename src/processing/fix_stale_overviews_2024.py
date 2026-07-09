"""
GeoAI-TKO · src/processing/fix_stale_overviews_2024.py
=======================================================
Фикс: overview-пирамида 2024_summer/s2_mosaic_cog.tif осталась в
устаревшем состоянии после Фазы B (verify_overview_2024.py показал 0/6
точек согласованы — full-res 0% nodata, overview 100% nodata). Тот же
паттерн, что был у 2025_summer/42TVN (см. fix_stale_overviews.py) —
build_overviews() внутри build_mosaic_2025.phase_b не отразил реально
записанные данные.

Структура (намеренно НЕ сокращённая, каждый шаг — отдельный файловый
хендл, чтобы исключить любой сценарий с недостаточным flush/кэшем):
  1. Закрыть текущий хендл файла полностью (файл уже закрыт — новый процесс)
  2. Заново открыть в "r+" на чистом хендле
  3. build_overviews() на свежем хендле
  4. Закрыть снова — гарантированный flush на диск
  5. Заново открыть ТОЛЬКО ДЛЯ ЧТЕНИЯ (независимый хендл) — 6 точек по
     всей площади, full-res vs overview (тот же метод, что
     verify_overview_2024.py)

Usage:
  python src/processing/fix_stale_overviews_2024.py
"""
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import rasterio.windows
from rasterio.enums import Resampling

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

COG_PATH = Path(r"D:\data\mosaics\2024_summer\s2_mosaic_cog.tif")
BAND_NAMES = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]
BLOCK = 64


def main():
    t0 = time.time()

    # ── Шаг 1-2: закрыть/переоткрыть на чистом хендле в r+ ─────────
    print("Шаг 1-2: открываю чистый хендл в r+...")
    dst = rasterio.open(COG_PATH, "r+")

    # ── Шаг 3: build_overviews на свежем хендле ─────────────────────
    print("Шаг 3: build_overviews([2,4,8,16,32,64], nearest)...")
    dst.build_overviews([2, 4, 8, 16, 32, 64], Resampling.nearest)
    dst.update_tags(ns="rio_overview", resampling="nearest")

    # ── Шаг 4: закрыть — гарантированный flush ──────────────────────
    print("Шаг 4: закрываю хендл (flush на диск)...")
    dst.close()
    print(f"  Overview rebuild занял {time.time()-t0:.0f}s")

    # ── Шаг 5: независимый read-only хендл, 6 точек по площади ──────
    print("\nШаг 5: открываю НЕЗАВИСИМЫЙ read-only хендл для верификации...")
    with rasterio.open(COG_PATH, "r") as ds:
        H, W = ds.height, ds.width
        file_nodata = ds.nodata if ds.nodata is not None else -9999.0

        fracs = [(0.15, 0.25), (0.5, 0.25), (0.85, 0.25),
                 (0.15, 0.75), (0.5, 0.75), (0.85, 0.75)]
        n_consistent = 0
        for fx, fy in fracs:
            col0 = int((fx * W) // BLOCK) * BLOCK
            row0 = int((fy * H) // BLOCK) * BLOCK
            col0 = min(col0, W - BLOCK)
            row0 = min(row0, H - BLOCK)

            full_block = ds.read(1, window=rasterio.windows.Window(col0, row0, BLOCK, BLOCK))
            full_nodata_pct = 100 * (full_block == file_nodata).sum() / full_block.size

            ov_pixel = ds.read(
                1, window=rasterio.windows.Window(col0, row0, BLOCK, BLOCK),
                out_shape=(1, 1),
            )[0, 0]
            ov_is_nodata = bool(ov_pixel == file_nodata)
            consistent = (full_nodata_pct == 100.0) == ov_is_nodata
            n_consistent += consistent
            print(f"  Точка (row0={row0}, col0={col0}): full-res nodata%={full_nodata_pct:.1f}%, "
                  f"overview nodata={ov_is_nodata} -> {'OK' if consistent else 'РАСХОЖДЕНИЕ'}")

        print(f"\n  {n_consistent}/6 точек согласованы")
        if n_consistent < 6:
            print("  ОШИБКА: overview всё ещё расходится с full-res — фикс НЕ сработал.")
            sys.exit(1)
        print("  OK — overview теперь совпадает с full-res на всех 6 точках")

    print(f"\nГотово за {time.time()-t0:.0f}s.")


if __name__ == "__main__":
    main()
