"""
GeoAI-TKO · src/processing/build_mosaic_2023_cdse.py
=======================================================
ЭТАП 3 (2023_summer CDSE rebuild) — Фаза A (репроекция+reflectance+
compositing на тайл) + Фаза B (windowed merge -> COG).

НЕ переписывает логику — импортирует phase_a/phase_b/baseline_offset/
dn_to_reflectance напрямую из build_mosaic_2025.py и переопределяет только
пути (REPROJ_DIR/COG_OUTPUT/COMPOSITING_LOG_PATH) через атрибуты модуля
(Python резолвит имена внутри phase_a/phase_b из module globals В МОМЕНТ
ВЫЗОВА, не при определении — переопределение атрибута модуля до вызова
работает корректно). Это гарантирует, что все уроки 2025 (Resampling.nearest
везде, interleave=band+bigtiff=YES на per-tile файлах, reflectance в одном
проходе с репроекцией, resume по out_path.exists()) применяются 2023 без
повторной реализации и без риска рассинхронизации логики между периодами.

Единственная адаптация — формат манифеста. 2023 CDSE манифест
(D:\\data\\s2_2023_cdse_raw\\manifest.json) хранит primary+fills под одним
tile_id ({"primary": {...}, "fills": [...]}), тогда как build_mosaic_2025.py
исторически ожидает раздельные manifest.json/fill_manifest.json. Здесь оба
представления собираются в памяти из единого 2023-манифеста перед вызовом
phase_a — сами phase_a/phase_b не тронуты.

Usage:
  python src/processing/build_mosaic_2023_cdse.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_mosaic_2025 as bm  # noqa: E402 — reuse phase_a/phase_b/baseline_offset as-is

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

MANIFEST_2023_PATH = Path(r"D:\data\s2_2023_cdse_raw\manifest.json")
REPROJ_DIR_2023 = Path(r"D:\data\s2_2023_cdse_reproj")
COG_OUTPUT_2023 = Path(r"D:\data\mosaics\2023_summer_cdse\s2_mosaic_cog.tif")
COMPOSITING_LOG_PATH_2023 = Path(r"D:\data\mosaics\2023_summer_cdse\compositing_stats.json")


def build_compat_manifests(manifest_2023: dict) -> tuple[dict, dict]:
    """Reshape our {tile: {primary, fills}} manifest into the two
    structures build_mosaic_2025.phase_a expects: a primary-only manifest
    ({"tiles": {tile: {product_id, cloud_cover, bands}}}) and a
    fill_manifest ({"tiles": {tile: [{product_id, bands}, ...]}})."""
    manifest = {"tiles": {}}
    fill_manifest = {"tiles": {}}
    for tile, entry in manifest_2023["tiles"].items():
        primary = entry["primary"]
        manifest["tiles"][tile] = {
            "product_id": primary["product_id"],
            "cloud_cover": primary["cloud_cover"],
            "bands": primary["bands"],
        }
        fill_manifest["tiles"][tile] = [
            {"product_id": f["product_id"], "bands": f["bands"]}
            for f in entry.get("fills", [])
        ]
    return manifest, fill_manifest


def main():
    if not MANIFEST_2023_PATH.exists():
        print(f"ОШИБКА: {MANIFEST_2023_PATH} не найден. Сначала Этап 2.")
        sys.exit(1)
    manifest_2023 = json.loads(MANIFEST_2023_PATH.read_text(encoding="utf-8"))
    if len(manifest_2023["tiles"]) != 43:
        print(f"ОШИБКА: ожидалось 43 тайла в манифесте, найдено {len(manifest_2023['tiles'])}")
        sys.exit(1)

    # Override module-level paths on the imported module — phase_a/phase_b
    # read these as free variables resolved from build_mosaic_2025's globals
    # at call time, so reassigning the attributes here redirects their I/O
    # to the 2023 tree without touching a single line of their logic.
    bm.REPROJ_DIR = REPROJ_DIR_2023
    bm.COG_OUTPUT = COG_OUTPUT_2023
    bm.COMPOSITING_LOG_PATH = COMPOSITING_LOG_PATH_2023

    print("=" * 70)
    print("  Этап 3 (2023_summer CDSE) — сборка мозаика (Фаза A + Фаза B)")
    print(f"  REPROJ_DIR = {bm.REPROJ_DIR}")
    print(f"  COG_OUTPUT = {bm.COG_OUTPUT}")
    print("=" * 70)

    manifest, fill_manifest = build_compat_manifests(manifest_2023)
    n_with_fills = sum(1 for v in fill_manifest["tiles"].values() if v)
    print(f"Манифест адаптирован: {len(manifest['tiles'])} тайлов, "
          f"{n_with_fills} с >=1 fill-продуктом")

    t0 = time.time()
    tile_results, compositing_stats = bm.phase_a(manifest, fill_manifest)
    size_gb = bm.phase_b(tile_results)

    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("  ИТОГ Этапа 3 (2023_summer CDSE)")
    print("=" * 70)
    print(f"  Тайлов смерджено: {len(tile_results)}")
    print(f"  Итоговый размер COG: {size_gb:.2f} GB")
    print(f"  Время: {elapsed/60:.1f} мин")
    print(f"  Файл: {bm.COG_OUTPUT}")
    print(f"  Формат: float32 reflectance, nodata={float(bm.FLOAT_NODATA)}")
    print("\n  Overviews построены внутри phase_b (close/reopen r+), но НЕЗАВИСИМАЯ")
    print("  read-only верификация (Урок 6, полный цикл) — отдельный шаг Этапа 4, не здесь.")


if __name__ == "__main__":
    main()
