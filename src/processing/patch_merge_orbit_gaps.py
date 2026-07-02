"""
GeoAI-TKO - src/processing/patch_merge_orbit_gaps.py
=======================================================
Точечный windowed patch-merge для 7 тайлов, у которых top-3-by-cloud отбор
(до фикса orbit-diversity в find_composite_candidates.py) взял кандидатов
только с одного relative orbit, оставив честный nodata на той половине
тайла, которую покрывает только другой orbit (см. gap_diagnosis.json).

НЕ полная пересборка 57GB COG — windowed read-modify-write на конкретных
7 окнах (по одному на тайл), r+ на уже готовом s2_mosaic_cog.tif. Offset/
reflectance-логика переиспользуется из build_mosaic_2025.py (не копия) —
иначе рискуем разойтись в конвенции offset между основным пайплайном и
патчем, как уже было с 2023 vs 2025.

Шаги:
  1. Для каждого из 7 тайлов — репроекция патч-продукта (JP2, uint16 DN)
     напрямую в окно главного COG (dst_transform строится из окна главного
     растра, не заново с нуля — иначе пиксельная сетка/фаза разойдётся).
  2. fill_mask = (текущее==nodata) & (патч валиден) — заполняем ТОЛЬКО дыры.
  3. Один dst.write() на весь блок (7 бэндов вместе), не по частям.
  4. Сразу после записи — верификация: nodata в окне реально уменьшился.
  5. После всех 7 патчей — ОДИН build_overviews() (не per-patch).
  6. Финальный regression-check по реальным пикселям (не geometry-оценка).

Usage:
  python src/processing/patch_merge_orbit_gaps.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds, transform as window_transform
from rasterio.warp import transform_bounds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_mosaic_2025 import (
    reproject_slot_to_reflectance, TARGET_CRS, BANDS, FLOAT_NODATA,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

COG_PATH = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")
GAP_DIAGNOSIS_PATH = Path(r"D:\data\gap_diagnosis.json")
RAW_DIR = Path(r"D:\data\s2_2025_raw")

PATCH_PRODUCTS = {
    "42TVN": "S2A_MSIL2A_20250813T062311_N0511_R034_T42TVN_20250813T084614",
    "42TUL": "S2B_MSIL2A_20250717T061639_N0511_R034_T42TUL_20250717T082859",
    "42TUR": "S2A_MSIL2A_20250826T063321_N0511_R077_T42TUR_20250826T080215",
    "42TWK": "S2B_MSIL2A_20250704T060639_N0511_R134_T42TWK_20250704T081213",
    "42TWN": "S2B_MSIL2A_20250627T061629_N0511_R034_T42TWN_20250627T082024",
    "42TWR": "S2B_MSIL2A_20250826T061629_N0511_R034_T42TWR_20250826T082527",
    "42TXM": "S2C_MSIL2A_20250828T060651_N0511_R134_T42TXM_20250828T095603",
}


def patch_bands(tile: str) -> dict:
    patch_dir = RAW_DIR / tile / "patch_orbit34"
    bands = {}
    for b in BANDS:
        p = patch_dir / f"{b}.jp2"
        if not p.exists():
            raise FileNotFoundError(f"{tile}: {p} не найден — патч не докачан")
        bands[b] = str(p)
    return bands


def tile_window_in_main_cog(tile: str, manifest: dict, main_ds) -> tuple:
    """Same bbox Phase A used for this tile's own reprojection grid (from the
    primary product's own B02), transformed into the main COG's CRS/pixel
    grid — guarantees pixel-exact alignment with what's already written."""
    primary_b02 = manifest["tiles"][tile]["bands"]["B02"]
    with rasterio.open(primary_b02) as ref:
        bounds_main_crs = transform_bounds(ref.crs, main_ds.crs, *ref.bounds)

    win = from_bounds(*bounds_main_crs, transform=main_ds.transform)
    # snap to whole pixels, clip to raster extent
    win = win.round_offsets().round_lengths()
    col_off = max(0, int(win.col_off))
    row_off = max(0, int(win.row_off))
    col_end = min(main_ds.width, int(win.col_off) + int(win.width))
    row_end = min(main_ds.height, int(win.row_off) + int(win.height))
    from rasterio.windows import Window
    win = Window(col_off, row_off, col_end - col_off, row_end - row_off)
    return win


def main():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    print("=" * 70)
    print(f"  Patch-merge — {len(PATCH_PRODUCTS)} orbit-seam тайлов")
    print("=" * 70)

    results = []
    t0 = time.time()

    with rasterio.open(COG_PATH, "r+") as dst:
        if dst.crs != TARGET_CRS:
            raise RuntimeError(f"COG CRS {dst.crs} != {TARGET_CRS} — прерываюсь до записи")
        if dst.count != len(BANDS) or set(dst.dtypes) != {"float32"}:
            raise RuntimeError(f"COG профиль неожиданный: count={dst.count}, dtypes={dst.dtypes}")
        main_nodata = dst.nodata

        for i, (tile, pid) in enumerate(PATCH_PRODUCTS.items(), 1):
            print(f"\n[{i}/{len(PATCH_PRODUCTS)}] {tile}: патч {pid[:40]}...")
            bands = patch_bands(tile)
            win = tile_window_in_main_cog(tile, manifest, dst)
            print(f"    окно в главном COG: col_off={win.col_off}, row_off={win.row_off}, "
                  f"w={win.width}, h={win.height}")

            dst_transform_win = window_transform(win, dst.transform)

            patch_arr, patch_valid = reproject_slot_to_reflectance(
                bands, pid, dst_transform_win, int(win.width), int(win.height),
            )

            current = dst.read(window=win)  # (7,H,W) float32
            current_nodata = current[0] == main_nodata

            fill_mask = current_nodata & patch_valid
            n_before = int(current_nodata.sum())
            n_fillable = int(fill_mask.sum())
            total_px = win.width * win.height

            new_block = current.copy()
            for b in range(len(BANDS)):
                new_block[b][fill_mask] = patch_arr[b][fill_mask]

            dst.write(new_block, window=win)

            # ── verification: read back, confirm nodata actually dropped ──
            readback = dst.read(window=win)
            n_after = int((readback[0] == main_nodata).sum())
            if n_after > n_before:
                raise RuntimeError(f"{tile}: nodata ВЫРОС после записи ({n_before}->{n_after}) — что-то не так, останавливаюсь")

            pct_before = 100 * n_before / total_px
            pct_after = 100 * n_after / total_px
            print(f"    nodata: {pct_before:.1f}% -> {pct_after:.1f}% "
                  f"(закрыто {n_fillable} px, {100*n_fillable/total_px:.1f}% окна)")

            results.append({
                "tile": tile, "product_id": pid,
                "total_px": total_px,
                "nodata_pct_before": round(pct_before, 2),
                "nodata_pct_after": round(pct_after, 2),
                "closed_px": n_fillable,
            })

        print("\n" + "=" * 70)
        print("  Перестраиваю overview pyramids (patch изменил пиксели внутри них)")
        print("=" * 70)
        dst.build_overviews([2, 4, 8, 16, 32, 64], Resampling.nearest)
        dst.update_tags(ns="rio_overview", resampling="nearest")

    elapsed = time.time() - t0
    print(f"\nГотово за {elapsed/60:.1f} мин.")

    print("\n" + "=" * 70)
    print("  ИТОГ patch-merge")
    print("=" * 70)
    all_ok = True
    for r in results:
        status = "OK" if r["nodata_pct_after"] < 5.0 else "ВНИМАНИЕ — всё ещё >5% nodata"
        if r["nodata_pct_after"] >= 5.0:
            all_ok = False
        print(f"  {r['tile']}: {r['nodata_pct_before']}% -> {r['nodata_pct_after']}%  [{status}]")

    out_path = Path(r"D:\data\patch_merge_results.json")
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Сохранено: {out_path}")
    print(f"  Все тайлы <5% nodata: {'ДА' if all_ok else 'НЕТ — см. выше'}")


if __name__ == "__main__":
    main()
