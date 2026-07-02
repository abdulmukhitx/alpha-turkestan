"""
GeoAI-TKO - src/processing/fix_stale_overviews.py
=======================================================
Фикс: overview-пирамида s2_mosaic_cog.tif осталась в pre-patch состоянии
после patch_merge_orbit_gaps.py, хотя build_overviews() отработал без
исключения — full-res данные корректны (проверено на всех 7 патч-тайлах),
но overview показывал устаревшие значения (напр. 42TVN: overview 61.45%
nodata при реальных 0.00%).

Структура (намеренно НЕ сокращённая, каждый шаг — отдельный файловый
хендл, чтобы исключить любой сценарий с недостаточным flush/кэшем):

  1. Закрыть текущий хендл файла полностью
  2. Заново открыть в "r+" на чистом хендле
  3. build_overviews() на свежем хендле
  4. Закрыть снова — гарантированный flush на диск
  5. Заново открыть ТОЛЬКО ДЛЯ ЧТЕНИЯ (независимый хендл) — прочитать
     overview для 42TVN, сравнить с full-res (должны совпасть, оба ~0%)
  6. Только после подтверждения через независимое чтение — перегенерировать
     preview.png и снова наложить границу для финальной визуальной проверки

Usage:
  python src/processing/fix_stale_overviews.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

COG_PATH = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")
PREVIEW_PATH = Path(r"D:\data\mosaics\2025_summer\preview.png")
BOUNDARY_PATH = Path(r"C:\Users\USER\alpha-turkestan\frontend\public\turkestan_boundary.geojson")


def tile_window(tile: str, manifest: dict, ds) -> rasterio.windows.Window:
    primary_b02 = manifest["tiles"][tile]["bands"]["B02"]
    with rasterio.open(primary_b02) as ref:
        bounds_main_crs = rasterio.warp.transform_bounds(ref.crs, ds.crs, *ref.bounds)
    win = from_bounds(*bounds_main_crs, transform=ds.transform).round_offsets().round_lengths()
    col_off = max(0, int(win.col_off))
    row_off = max(0, int(win.row_off))
    col_end = min(ds.width, int(win.col_off) + int(win.width))
    row_end = min(ds.height, int(win.row_off) + int(win.height))
    return rasterio.windows.Window(col_off, row_off, col_end - col_off, row_end - row_off)


def main():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
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

    # ── Шаг 5: независимый read-only хендл, verify 42TVN ────────────
    print("\nШаг 5: открываю НЕЗАВИСИМЫЙ read-only хендл для верификации...")
    with rasterio.open(COG_PATH, "r") as verify_ds:
        nodata = verify_ds.nodata
        win = tile_window("42TVN", manifest, verify_ds)

        # full-res
        band0_full = verify_ds.read(1, window=win)
        pct_full = 100 * np.sum(band0_full == nodata) / band0_full.size

        # coarsest overview, explicit nearest (same as build)
        factor = max(verify_ds.overviews(1))
        out_h = max(1, win.height // factor)
        out_w = max(1, win.width // factor)
        band0_ovr = verify_ds.read(1, window=win, out_shape=(out_h, out_w), resampling=Resampling.nearest)
        pct_ovr = 100 * np.sum(band0_ovr == nodata) / band0_ovr.size

        print(f"  42TVN full-res nodata: {pct_full:.2f}%")
        print(f"  42TVN overview (factor={factor}) nodata: {pct_ovr:.2f}%")

        if abs(pct_full - pct_ovr) > 1.0:
            print(f"\n  ОШИБКА: overview всё ещё расходится с full-res "
                  f"({pct_ovr:.2f}% vs {pct_full:.2f}%) — фикс НЕ сработал, останавливаюсь.")
            sys.exit(1)
        print(f"  OK — overview теперь совпадает с full-res (разница {abs(pct_full-pct_ovr):.2f}pp)")

    # ── Шаг 6: перегенерировать preview.png + наложить границу ───────
    print("\nШаг 6: перегенерирую preview.png (только после подтверждения на Шаге 5)...")
    from PIL import Image, ImageDraw
    from shapely.geometry import shape
    from rasterio.warp import transform_geom

    with rasterio.open(COG_PATH, "r") as ds:
        file_nodata = ds.nodata
        ovr = ds.overviews(1)
        factor = max(ovr) if ovr else 1
        out_h = max(1, ds.height // factor)
        out_w = max(1, ds.width // factor)
        rgb = ds.read([3, 2, 1], out_shape=(3, out_h, out_w)).astype(np.float32)
        rgb_valid = rgb[rgb != file_nodata]
        p2, p98 = np.percentile(rgb_valid, [2, 98]) if rgb_valid.size else (0, 1)
        rgb[rgb == file_nodata] = 0.0
        rgb = np.clip((rgb - p2) / (p98 - p2 + 1e-6), 0, 1)
        rgb_u8 = (rgb * 255).astype(np.uint8)
        img = Image.fromarray(np.transpose(rgb_u8, (1, 2, 0)), mode="RGB")
        img.save(PREVIEW_PATH)
        print(f"  Сохранено: {PREVIEW_PATH} ({img.width}x{img.height}px)")

        full_transform = ds.transform
        full_width, full_height = ds.width, ds.height
        crs = ds.crs

    prev_w, prev_h = img.size
    scale_x = prev_w / full_width
    scale_y = prev_h / full_height

    geo = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
    feats = geo.get("features", [geo])
    img_b = img.convert("RGB").copy()
    draw = ImageDraw.Draw(img_b)
    for feat in feats:
        geom_4326 = feat.get("geometry", feat)
        geom_proj = transform_geom("EPSG:4326", crs, geom_4326)
        g = shape(geom_proj)
        polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
        for poly in polys:
            pixel_coords = []
            for x, y in poly.exterior.coords:
                row, col = rasterio.transform.rowcol(full_transform, x, y)
                pixel_coords.append((col * scale_x, row * scale_y))
            draw.line(pixel_coords, fill=(255, 0, 0), width=2)
    out_b = PREVIEW_PATH.parent / "preview_with_boundary.png"
    img_b.save(out_b)
    print(f"  Сохранено (с границей): {out_b}")

    print(f"\nГотово за {time.time()-t0:.0f}s.")


if __name__ == "__main__":
    main()
