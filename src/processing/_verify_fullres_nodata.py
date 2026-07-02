import json
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
from rasterio.windows import from_bounds

COG_PATH = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")
manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

TILES_TO_CHECK = ["42TVN", "42TUL", "42TUR", "42TWK", "42TWN", "42TWR", "42TXM"]

PATCH_MERGE_RESULTS = json.loads(Path(r"D:\data\patch_merge_results.json").read_text(encoding="utf-8"))
before_after = {r["tile"]: r for r in PATCH_MERGE_RESULTS}

results_table = []
with rasterio.open(COG_PATH) as ds:
    nodata = ds.nodata
    for tile in TILES_TO_CHECK:
        primary_b02 = manifest["tiles"][tile]["bands"]["B02"]
        with rasterio.open(primary_b02) as ref:
            bounds_main_crs = rasterio.warp.transform_bounds(ref.crs, ds.crs, *ref.bounds)
        win = from_bounds(*bounds_main_crs, transform=ds.transform)
        win = win.round_offsets().round_lengths()
        col_off = max(0, int(win.col_off))
        row_off = max(0, int(win.row_off))
        col_end = min(ds.width, int(win.col_off) + int(win.width))
        row_end = min(ds.height, int(win.row_off) + int(win.height))
        w = col_end - col_off
        h = row_end - row_off
        win = rasterio.windows.Window(col_off, row_off, w, h)

        print(f"\n{tile}: окно ({col_off},{row_off}) {w}x{h} px, читаю B02 полностью (full-res)...")
        band0 = ds.read(1, window=win)  # full resolution, only B02 to keep memory sane
        total = band0.size
        n_nodata = int(np.sum(band0 == nodata))
        pct = 100 * n_nodata / total
        print(f"  Реальный nodata (full-res, band B02): {n_nodata}/{total} = {pct:.2f}%")
        pm = before_after.get(tile, {})
        results_table.append({
            "tile": tile,
            "nodata_before_patch_pct": pm.get("nodata_pct_before"),
            "nodata_after_patch_pct_reported": pm.get("nodata_pct_after"),
            "nodata_fullres_now_pct": round(pct, 4),
        })

print("\n" + "=" * 70)
print("  ИТОГ — full-res проверка всех 7 патч-тайлов")
print("=" * 70)
print(f"{'Тайл':>8} {'до патча':>10} {'после (отчёт)':>15} {'full-res СЕЙЧАС':>17}")
all_clean = True
for r in results_table:
    flag = ""
    if r["nodata_fullres_now_pct"] > 1.0:
        flag = "  <-- РЕАЛЬНЫЙ nodata, патч не сработал!"
        all_clean = False
    print(f"{r['tile']:>8} {r['nodata_before_patch_pct']:>9}% {r['nodata_after_patch_pct_reported']:>14}% "
          f"{r['nodata_fullres_now_pct']:>16}%{flag}")

print(f"\nВсе 7 тайлов чисты на full-res (<1%): {'ДА' if all_clean else 'НЕТ — см. выше'}")
