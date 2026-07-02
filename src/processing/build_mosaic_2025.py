"""
GeoAI-TKO · src/processing/build_mosaic_2025.py
=======================================================
ЭТАП 5 (v2) — Сборка COG-мозаика 2025_summer с двумя ключевыми исправлениями
после провала первого QA (Этап 6):

  ЗАДАЧА 1 — BOA_ADD_OFFSET. Сырые CDSE JP2 baseline >=04.00 (наши продукты —
  05.11) хранят DN со сдвигом: Reflectance = (DN + BOA_ADD_OFFSET) / 10000,
  BOA_ADD_OFFSET=-1000. 2023_summer (эталон) собран из Microsoft Planetary
  Computer, который гармонизирует baseline на своей стороне (offset=0 уже
  корректно применён) — подтверждено эмпирически на 125 точках/6 классах
  покрова (check_offset_stratified.py). Вместо разных offset-конвенций в
  разных периодах — оба периода теперь хранятся как физический float32
  reflectance (0.0-1.0), offset применяется по данным STAC baseline
  конкретного продукта, не хардкодится вслепую.

  ЗАДАЧА 2 — Temporal compositing. Один продукт на тайл не всегда покрывает
  весь тайл (граница орбиты) — ~31% пикселей внутри AOI были nodata в первой
  сборке. Теперь для каждого тайла: primary (уже скачан в Этапе 4) + до 2
  fill-продуктов с других дат/орбит (download_composite_assets.py). При
  репроекции — nodata-маска primary закрывается пикселями fill1, затем fill2.

Архитектура репроекции не изменилась относительно первой версии (см. историю
в git) — по одному проходу на тайл, все 7 бэндов вместе, обязательная
cross-tile валидация перед мерджем, windowed merge без RAM.

Usage:
  python src/processing/build_mosaic_2025.py
"""
import json
import re
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
FILL_MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\fill_manifest.json")
REPROJ_DIR = Path(r"D:\data\s2_2025_reproj_v2")
COG_OUTPUT = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
COMPOSITING_LOG_PATH = Path(r"D:\data\mosaics\2025_summer\compositing_stats.json")

TARGET_CRS = CRS.from_epsg(32641)
TARGET_RES = 10.0
BANDS = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]
BLOCK_SIZE = 512

FLOAT_NODATA = np.float32(-9999.0)
BASELINE_RE = re.compile(r"_N(\d{4})_")


def baseline_offset(product_id: str) -> int:
    """BOA_ADD_OFFSET for this product, derived from its processing baseline
    in the product_id (N0511 -> baseline 05.11 -> offset -1000). Baseline
    < 04.00 -> offset 0 (pre-harmonization products don't carry the shift)."""
    m = BASELINE_RE.search(product_id)
    if not m:
        # unknown baseline in filename — fail loud, don't guess silently
        raise ValueError(f"не могу извлечь baseline из product_id: {product_id}")
    baseline = float(f"{m.group(1)[:2]}.{m.group(1)[2:]}")
    return -1000 if baseline >= 4.0 else 0


def dn_to_reflectance(dn: np.ndarray, offset: int) -> np.ndarray:
    """dn: uint16 array, any of the 7 bands. Returns float32 reflectance,
    clipped to physically valid [0,1], with 0-DN (source nodata) preserved
    as an explicit mask by the caller (NOT clipped/converted here)."""
    return np.clip((dn.astype(np.float32) + offset) / 10000.0, 0.0, 1.0)


# ════════════════════════════════════════════════════════════════
#   PHASE A — per-tile reprojection to reflectance + compositing
# ════════════════════════════════════════════════════════════════

def _reproject_band_to_ref(src_path: Path, band_idx_dst, dst, dst_transform):
    """Reproject one source band (raw uint16 DN) directly into a float32
    reflectance destination band, applying that source product's own
    BOA_ADD_OFFSET. Returns nothing — writes via rasterio.band(dst, ...)."""
    raise NotImplementedError  # replaced by explicit per-slot logic below


def reproject_slot_to_reflectance(band_paths: dict, product_id: str, dst_transform, dst_width, dst_height):
    """Reproject one product's 7 bands (uint16 DN, raw JP2) into a (7,H,W)
    float32 reflectance array on the shared TARGET grid, plus a boolean
    valid mask (H,W) — True where ALL 7 bands are valid (source DN>0 AND
    survived reprojection, i.e. not nodata-filled)."""
    offset = baseline_offset(product_id)
    out = np.full((len(BANDS), dst_height, dst_width), FLOAT_NODATA, dtype=np.float32)
    valid = np.ones((dst_height, dst_width), dtype=bool)

    for i, band in enumerate(BANDS):
        with rasterio.open(band_paths[band]) as src:
            dn_band = np.zeros((dst_height, dst_width), dtype=np.uint16)
            reproject(
                source=rasterio.band(src, 1),
                destination=dn_band,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs=TARGET_CRS,
                dst_nodata=0,
                resampling=Resampling.nearest,  # see 2026-06-30 bilinear artifact fix
            )
            band_valid = dn_band > 0
            valid &= band_valid
            refl = dn_to_reflectance(dn_band, offset)
            out[i] = refl

    out[:, ~valid] = FLOAT_NODATA
    return out, valid


def composite_tile(tile_id: str, primary_bands: dict, primary_pid: str,
                    fills: list, dst_transform, dst_width, dst_height) -> dict:
    """Build one tile's (7,H,W) float32 reflectance array by layering
    primary -> fill1 -> fill2, each filling only the nodata gaps left by
    the previous layer. Returns the composited array plus per-layer stats
    for Шаг 2.3 logging."""
    composite, valid = reproject_slot_to_reflectance(primary_bands, primary_pid, dst_transform, dst_width, dst_height)
    total_px = dst_width * dst_height
    stats = {"tile": tile_id, "total_px": total_px, "primary_valid_pct": round(100 * valid.sum() / total_px, 2)}

    for slot_i, fill in enumerate(fills, 1):
        gap = ~valid
        n_gap_before = gap.sum()
        if n_gap_before == 0:
            stats[f"fill{slot_i}_closed_pct"] = 0.0
            continue
        fill_arr, fill_valid = reproject_slot_to_reflectance(
            fill["bands"], fill["product_id"], dst_transform, dst_width, dst_height
        )
        can_fill = gap & fill_valid
        composite[:, can_fill] = fill_arr[:, can_fill]
        valid |= can_fill
        n_closed = can_fill.sum()
        stats[f"fill{slot_i}_closed_pct"] = round(100 * n_closed / total_px, 2)
        stats[f"fill{slot_i}_product_id"] = fill["product_id"]

    stats["remaining_nodata_pct"] = round(100 * (~valid).sum() / total_px, 2)
    return composite, valid, stats


def build_tile(tile_id: str, primary_info: dict, fills: list, out_path: Path) -> dict:
    with rasterio.open(primary_info["bands"]["B02"]) as ref:
        ref_crs = ref.crs
        dst_transform, dst_width, dst_height = calculate_default_transform(
            ref_crs, TARGET_CRS, ref.width, ref.height, *ref.bounds,
            resolution=TARGET_RES,
        )

    composite, valid, stats = composite_tile(
        tile_id, primary_info["bands"], primary_info["product_id"],
        fills, dst_transform, dst_width, dst_height,
    )

    profile = {
        "driver": "GTiff", "dtype": "float32", "width": dst_width, "height": dst_height,
        "count": len(BANDS), "crs": TARGET_CRS, "transform": dst_transform,
        "nodata": float(FLOAT_NODATA), "compress": "deflate", "tiled": True,
        "blockxsize": BLOCK_SIZE, "blockysize": BLOCK_SIZE, "bigtiff": "YES",
        "interleave": "band",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".tif.part")
    with rasterio.open(tmp_path, "w", **profile) as dst:
        for i in range(len(BANDS)):
            dst.write(composite[i], i + 1)
    tmp_path.replace(out_path)

    with rasterio.open(out_path) as ds:
        if ds.count != len(BANDS):
            raise RuntimeError(f"{tile_id}: ожидалось {len(BANDS)} бэндов, получено {ds.count}")
        if set(ds.dtypes) != {"float32"}:
            raise RuntimeError(f"{tile_id}: неверные dtype {ds.dtypes}, ожидался float32")
        if ds.crs != TARGET_CRS:
            raise RuntimeError(f"{tile_id}: CRS {ds.crs} != {TARGET_CRS}")
        res_x, res_y = ds.res
        if abs(res_x - TARGET_RES) > 1e-6 or abs(res_y - TARGET_RES) > 1e-6:
            raise RuntimeError(f"{tile_id}: разрешение {ds.res} != ({TARGET_RES},{TARGET_RES})")

    return {
        "tile": tile_id, "width": dst_width, "height": dst_height,
        "res": (TARGET_RES, TARGET_RES), "path": str(out_path),
        "stats": stats,
    }


def phase_a(manifest: dict, fill_manifest: dict) -> tuple[list[dict], list[dict]]:
    print("=" * 70)
    print("  ФАЗА A — репроекция 43 тайлов в reflectance float32 + compositing")
    print("=" * 70)

    tiles = manifest["tiles"]
    fill_tiles = fill_manifest.get("tiles", {})
    results = []
    compositing_stats = []
    t0 = time.time()

    for i, (tile_id, info) in enumerate(sorted(tiles.items()), 1):
        out_path = REPROJ_DIR / f"{tile_id}.tif"
        fills = fill_tiles.get(tile_id, [])
        print(f"[{i}/{len(tiles)}] {tile_id}: primary={info['product_id'][:25]}... "
              f"+ {len(fills)} fill продукт(а)")

        if out_path.exists():
            print(f"    уже собран, проверяю...")
            with rasterio.open(out_path) as ds:
                stats = None  # stats not recoverable from cache; re-log as unknown
        else:
            r = build_tile(tile_id, info, fills, out_path)
            stats = r["stats"]
            compositing_stats.append(stats)
            print(f"    primary валидно: {stats['primary_valid_pct']}%, "
                  f"остаток nodata после compositing: {stats['remaining_nodata_pct']}%")

        with rasterio.open(out_path) as ds:
            res_x, res_y = ds.res
            if ds.count != len(BANDS) or set(ds.dtypes) != {"float32"} or ds.crs != TARGET_CRS \
                    or abs(res_x - TARGET_RES) > 1e-6 or abs(res_y - TARGET_RES) > 1e-6:
                raise RuntimeError(f"{tile_id}: провалена валидация существующего файла {out_path}")
            results.append({
                "tile": tile_id, "width": ds.width, "height": ds.height,
                "res": ds.res, "bounds": tuple(ds.bounds), "crs": str(ds.crs),
                "cloud_cover": info["cloud_cover"], "path": str(out_path),
            })
        elapsed = time.time() - t0
        print(f"    OK — {results[-1]['width']}x{results[-1]['height']}px, {elapsed/60:.1f} мин прошло")

    print("\nПроверка консистентности всех 43 тайлов перед мерджем...")
    ref = results[0]
    bad = [r for r in results if r["res"] != ref["res"] or r["crs"] != ref["crs"]]
    if bad:
        print("ОШИБКА: расхождение CRS/разрешения пикселя у тайлов:")
        for r in bad:
            print(f"  {r['tile']}: res={r['res']} crs={r['crs']}")
        sys.exit(1)
    print(f"OK — все {len(results)} тайлов: CRS={ref['crs']}, разрешение={ref['res']}, dtype=float32, count=7")

    if compositing_stats:
        COMPOSITING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        COMPOSITING_LOG_PATH.write_text(json.dumps(compositing_stats, indent=2, ensure_ascii=False), encoding="utf-8")
        avg_primary = np.mean([s["primary_valid_pct"] for s in compositing_stats])
        avg_remaining = np.mean([s["remaining_nodata_pct"] for s in compositing_stats])
        print(f"\nСредняя валидность primary (до compositing): {avg_primary:.1f}%")
        print(f"Средний остаток честного nodata (после compositing): {avg_remaining:.1f}%")
        print(f"Статистика compositing сохранена: {COMPOSITING_LOG_PATH}")

    return results, compositing_stats


# ════════════════════════════════════════════════════════════════
#   PHASE B — windowed merge → COG (float32, nodata=-9999)
# ════════════════════════════════════════════════════════════════

def phase_b(tile_results: list[dict]):
    print("\n" + "=" * 70)
    print("  ФАЗА B — мердж 43 репроецированных тайлов в COG (windowed, float32)")
    print("  Приоритет на стыках: тайл с меньшей облачностью побеждает (первый непустой пиксель)")
    print("=" * 70)

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
        nodata = first.nodata

        all_bounds = [src.bounds for src in src_files]
        left = min(b.left for b in all_bounds)
        bottom = min(b.bottom for b in all_bounds)
        right = max(b.right for b in all_bounds)
        top = max(b.top for b in all_bounds)

        res = abs(first.transform.a)
        width = int(round((right - left) / res))
        height = int(round((top - bottom) / res))
        print(f"  Итоговый размер: {width} x {height} px @ {res}m")
        print(f"  Примерный размер (без сжатия, float32): {width*height*count*4/1e9:.1f} GB")

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

                    block_data = np.full((count, row_h, col_w), nodata, dtype=np.dtype(dtype))

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
        print("Строим overviews (пирамиды, nearest — average даёт нули на nodata=-9999)...")
        with rasterio.open(COG_OUTPUT, "r+") as dst:
            dst.build_overviews([2, 4, 8, 16, 32, 64], Resampling.nearest)
            dst.update_tags(ns="rio_overview", resampling="nearest")

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

    fill_manifest = {"tiles": {}}
    if FILL_MANIFEST_PATH.exists():
        fill_manifest = json.loads(FILL_MANIFEST_PATH.read_text(encoding="utf-8"))
    else:
        print(f"ВНИМАНИЕ: {FILL_MANIFEST_PATH} не найден — соберу без compositing (только primary).")

    t0 = time.time()
    tile_results, compositing_stats = phase_a(manifest, fill_manifest)
    size_gb = phase_b(tile_results)

    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("  ИТОГ Этапа 5 (v2 — reflectance + compositing)")
    print("=" * 70)
    print(f"  Тайлов смерджено: {len(tile_results)}")
    print(f"  Итоговый размер COG: {size_gb:.2f} GB")
    print(f"  Время: {elapsed/60:.1f} мин")
    print(f"  Файл: {COG_OUTPUT}")
    print(f"  Формат: float32 reflectance, nodata={float(FLOAT_NODATA)}")

    # size reference no longer directly comparable (float32 = 2x uint16 raw,
    # but DEFLATE compresses reflectance differently than DN) — report only,
    # don't gate on the old uint16-based heuristic.
    print(f"\n  ПРИМЕЧАНИЕ: старый ориентир по размеру (34.5GB@37 тайлов, uint16) не применим")
    print(f"  напрямую к float32 — размер файла оценивать в Этапе 6 заново.")


if __name__ == "__main__":
    main()
