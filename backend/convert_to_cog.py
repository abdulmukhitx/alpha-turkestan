"""
Merge 37 tile TIFs → COG без GDAL и без загрузки в RAM
=======================================================
Использует rasterio который уже установлен в твоём venv.
Читает тайлы окнами (windowed) — не грузит всё в память.

Запуск:
    python convert_to_cog.py
"""

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.merge import merge
from rasterio.transform import from_bounds
from rasterio.warp import transform_bounds

# ── Конфиг ──────────────────────────────────────────────────────────────────
TILES_DIR  = Path(r"C:\Users\USER\alpha-turkestan\src\processing\s2_work")
COG_OUTPUT = Path(r"D:\data\mosaics\2023_summer\s2_mosaic_cog.tif")

# Размер окна записи — чем меньше, тем меньше RAM (256 = ~50 MB на запись)
BLOCK_SIZE = 512


def find_tiles():
    tiles = sorted(TILES_DIR.glob("tile_*.tif"))
    if not tiles:
        tiles = sorted(Path(".").glob("tile_*.tif"))
    return tiles


def merge_to_cog(tiles):
    print(f"Открываем {len(tiles)} тайлов...")
    src_files = []
    try:
        for t in tiles:
            src_files.append(rasterio.open(t))

        # Берём метаданные из первого тайла
        first = src_files[0]
        count  = first.count
        dtype  = first.dtypes[0]
        crs    = first.crs
        nodata = first.nodata

        print(f"  Банд: {count}, dtype: {dtype}, CRS: {crs}")

        # Вычисляем общий bbox всех тайлов
        all_bounds = [src.bounds for src in src_files]
        left   = min(b.left   for b in all_bounds)
        bottom = min(b.bottom for b in all_bounds)
        right  = max(b.right  for b in all_bounds)
        top    = max(b.top    for b in all_bounds)

        res = abs(first.transform.a)  # разрешение в метрах
        width  = int((right - left)   / res)
        height = int((top   - bottom) / res)

        print(f"  Итоговый размер: {width} × {height} пкс @ {res}м")
        print(f"  Примерный размер файла: {width * height * count * 2 / 1e9:.1f} GB")

        transform = from_bounds(left, bottom, right, top, width, height)

        # Профиль для COG
        profile = {
            "driver":    "GTiff",
            "dtype":     dtype,
            "width":     width,
            "height":    height,
            "count":     count,
            "crs":       crs,
            "transform": transform,
            "nodata":    nodata if nodata is not None else 0,
            "compress":  "deflate",
            "tiled":     True,
            "blockxsize": BLOCK_SIZE,
            "blockysize": BLOCK_SIZE,
            "bigtiff":   "YES",
            "interleave": "band",
        }

        COG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

        print(f"\nЗаписываем → {COG_OUTPUT}")
        print("Читаем построчно блоками — RAM не перегружается...")

        # Считаем сколько блоков по высоте
        n_blocks_y = (height + BLOCK_SIZE - 1) // BLOCK_SIZE
        n_blocks_x = (width  + BLOCK_SIZE - 1) // BLOCK_SIZE
        total_blocks = n_blocks_y * n_blocks_x

        with rasterio.open(COG_OUTPUT, "w", **profile) as dst:
            done = 0
            for row_off in range(0, height, BLOCK_SIZE):
                row_h = min(BLOCK_SIZE, height - row_off)

                for col_off in range(0, width, BLOCK_SIZE):
                    col_w = min(BLOCK_SIZE, width - col_off)

                    # Координаты этого блока в пространстве
                    block_left   = left + col_off * res
                    block_right  = left + (col_off + col_w) * res
                    block_top    = top  - row_off * res
                    block_bottom = top  - (row_off + row_h) * res

                    # Читаем данные из каждого тайла который перекрывает блок
                    block_data = np.zeros((count, row_h, col_w),
                                          dtype=np.dtype(dtype))

                    for src in src_files:
                        # Проверяем пересечение с тайлом
                        if (src.bounds.right  <= block_left  or
                            src.bounds.left   >= block_right or
                            src.bounds.top    <= block_bottom or
                            src.bounds.bottom >= block_top):
                            continue  # тайл не пересекает этот блок

                        # Окно чтения внутри тайла
                        win = rasterio.windows.from_bounds(
                            max(block_left,   src.bounds.left),
                            max(block_bottom, src.bounds.bottom),
                            min(block_right,  src.bounds.right),
                            min(block_top,    src.bounds.top),
                            src.transform,
                        )

                        # Окно записи в результирующий блок
                        dst_col = int((max(block_left, src.bounds.left) - block_left) / res)
                        dst_row = int((block_top - min(block_top, src.bounds.top)) / res)
                        read_w  = int(win.width)
                        read_h  = int(win.height)

                        if read_w <= 0 or read_h <= 0:
                            continue

                        try:
                            data = src.read(
                                window=win,
                                out_shape=(count, read_h, read_w),
                                resampling=Resampling.nearest,
                            )
                            # Первый непустой пиксель побеждает (как method="first")
                            valid = data[0] != (nodata if nodata else 0)
                            for b in range(count):
                                dst_slice = block_data[
                                    b,
                                    dst_row:dst_row + read_h,
                                    dst_col:dst_col + read_w,
                                ]
                                src_slice = data[b, :read_h, :read_w]
                                mask = valid[:read_h, :read_w]
                                dst_slice[mask] = src_slice[mask]
                        except Exception:
                            pass

                    # Записываем блок
                    dst.write(
                        block_data,
                        window=rasterio.windows.Window(col_off, row_off, col_w, row_h),
                    )

                    done += 1
                    if done % 10 == 0 or done == total_blocks:
                        pct = done / total_blocks * 100
                        print(f"  [{pct:5.1f}%] блок {done}/{total_blocks}", end="\r")

        print(f"\n✓ Записано: {COG_OUTPUT}")

        # Добавляем обзорные уровни (пирамиды) для быстрой загрузки на мелких зумах
        print("\nСтроим обзорные уровни (пирамиды)...")
        with rasterio.open(COG_OUTPUT, "r+") as dst:
            overviews = [2, 4, 8, 16, 32, 64]
            dst.build_overviews(overviews, Resampling.bilinear)
            dst.update_tags(ns="rio_overview", resampling="bilinear")

        size_gb = COG_OUTPUT.stat().st_size / 1e9
        print(f"✓ Пирамиды добавлены")
        print(f"✓ Итоговый размер: {size_gb:.1f} GB")

    finally:
        for src in src_files:
            src.close()


if __name__ == "__main__":
    print("=" * 55)
    print("GeoAI-TKO — Merge tiles → COG (без GDAL, без RAM)")
    print("=" * 55)

    tiles = find_tiles()
    if not tiles:
        print(f"ОШИБКА: тайлы не найдены в {TILES_DIR}")
        print("Измени TILES_DIR в начале скрипта.")
        sys.exit(1)

    print(f"Найдено тайлов: {len(tiles)}")
    for t in tiles:
        print(f"  {t.name}")

    merge_to_cog(tiles)

    print("\n" + "=" * 55)
    print("Готово! Обнови COG_PATH в main.py:")
    print(f'  COG_PATH = r"{COG_OUTPUT}"')
    print("=" * 55)