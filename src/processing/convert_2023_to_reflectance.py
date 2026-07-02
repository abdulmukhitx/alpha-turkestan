"""
GeoAI-TKO · src/processing/convert_2023_to_reflectance.py
=======================================================
ЗАДАЧА 3 — конвертация 2023_summer из сырых DN (uint16) в физический
reflectance (float32, 0.0-1.0), тот же формат, что и новый 2025_summer.

2023_summer собран из Microsoft Planetary Computer, который гармонизирует
Sentinel-2 processing baseline на своей стороне — эмпирически подтверждено
(check_offset_stratified.py, 125 точек/6 классов, ratio~1.0 при offset=0
у 2023), поэтому здесь BOA_ADD_OFFSET=0: reflectance = DN / 10000.

СТАРЫЙ uint16 файл НЕ трогается/не удаляется — backend продолжает на нём
работать до отдельного шага миграции. Результат — s2_mosaic_cog_v2.tif
рядом со старым.

Usage:
  python src/processing/convert_2023_to_reflectance.py
"""
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

SRC_COG = Path(r"D:\data\mosaics\2023_summer\s2_mosaic_cog.tif")
DST_COG = Path(r"D:\data\mosaics\2023_summer\s2_mosaic_cog_v2.tif")
BLOCK_SIZE = 512
OFFSET_2023 = 0  # confirmed empirically — Planetary Computer harmonized source
FLOAT_NODATA = np.float32(-9999.0)


def main():
    print("=" * 70)
    print("  Задача 3 — конвертация 2023_summer в reflectance float32")
    print("=" * 70)

    if DST_COG.exists():
        print(f"ВНИМАНИЕ: {DST_COG} уже существует — перезаписываю.")

    with rasterio.open(SRC_COG) as src:
        print(f"Источник: {SRC_COG}")
        print(f"  {src.width}x{src.height}px, {src.count} бэндов, dtype={src.dtypes[0]}, nodata={src.nodata}")
        src_nodata = src.nodata if src.nodata is not None else 0

        profile = src.profile.copy()
        profile.update({
            "dtype": "float32",
            "nodata": float(FLOAT_NODATA),
            "compress": "deflate",
            "tiled": True,
            "blockxsize": BLOCK_SIZE,
            "blockysize": BLOCK_SIZE,
            "bigtiff": "YES",
            "interleave": "pixel",
        })

        n_blocks_y = (src.height + BLOCK_SIZE - 1) // BLOCK_SIZE
        n_blocks_x = (src.width + BLOCK_SIZE - 1) // BLOCK_SIZE
        total_blocks = n_blocks_y * n_blocks_x
        print(f"Ожидаемый размер (без сжатия): {src.width*src.height*src.count*4/1e9:.1f} GB (float32)")

        t0 = time.time()
        DST_COG.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(DST_COG, "w", **profile) as dst:
            done = 0
            for row_off in range(0, src.height, BLOCK_SIZE):
                row_h = min(BLOCK_SIZE, src.height - row_off)
                for col_off in range(0, src.width, BLOCK_SIZE):
                    col_w = min(BLOCK_SIZE, src.width - col_off)
                    win = rasterio.windows.Window(col_off, row_off, col_w, row_h)

                    dn = src.read(window=win)  # (count, h, w) uint16
                    valid = np.all(dn > 0, axis=0) if src_nodata == 0 else np.all(dn != src_nodata, axis=0)

                    refl = np.clip((dn.astype(np.float32) + OFFSET_2023) / 10000.0, 0.0, 1.0)
                    refl[:, ~valid] = FLOAT_NODATA

                    dst.write(refl, window=win)

                    done += 1
                    if done % 50 == 0 or done == total_blocks:
                        pct = done / total_blocks * 100
                        elapsed = time.time() - t0
                        print(f"  [{pct:5.1f}%] блок {done}/{total_blocks}, {elapsed/60:.1f} мин")

        print(f"\nЗаписано: {DST_COG}")
        print("Строим overviews (nearest — average даёт нули на nodata=-9999)...")
        with rasterio.open(DST_COG, "r+") as dst:
            dst.build_overviews([2, 4, 8, 16, 32, 64], Resampling.nearest)
            dst.update_tags(ns="rio_overview", resampling="nearest")

        size_gb = DST_COG.stat().st_size / 1e9
        print(f"\nГотово. Итоговый размер: {size_gb:.2f} GB")
        print(f"Старый файл НЕ тронут: {SRC_COG}")


if __name__ == "__main__":
    main()
