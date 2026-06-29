"""
Пересборка COG — умный отбор: 1 лучшая сцена на каждый тайл
Покрывает всю область минимальным количеством сцен
"""

import os
import shutil
from pathlib import Path
from collections import defaultdict
import numpy as np
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.merge import merge
from rasterio.mask import mask
from shapely.geometry import shape, mapping
import geopandas as gpd
from pystac_client import Client
import planetary_computer as pc

# ============================================================
# КОНФИГ
# ============================================================

AOI_GEOJSON = {
    "type": "Polygon",
    "coordinates": [[[66.75293,46.088472],[69.32373,46.103709],
    [70.268555,43.707594],[71.026611,42.261049],
    [68.675537,40.5472],[68.24707,40.497092],
    [67.91748,40.713956],[66.665039,41.095912],
    [66.027832,41.607228],[65.610352,42.309815],
    [67.565918,43.659924],[67.016602,44.762337],
    [66.75293,46.088472]]]
}

DATE_RANGE  = "2023-06-01/2023-09-30"
MAX_CLOUD   = 60
TARGET_CRS  = "EPSG:32641"
OUTPUT_COG  = r"D:\data\s2_mosaic_cog_v2.tif"
BACKUP_COG  = r"D:\data\s2_mosaic_cog_backup.tif"
OLD_COG     = r"D:\data\s2_mosaic_cog.tif"
TEMP_DIR    = Path(r"D:\data\s2_temp_v2")
BANDS       = ["B02","B03","B04","B05","B08","B8A","B11"]

# ============================================================
# ШАГ 0 — Бэкап
# ============================================================

if Path(OLD_COG).exists() and not Path(BACKUP_COG).exists():
    print(f"Сохраняю бэкап...")
    shutil.copy2(OLD_COG, BACKUP_COG)
    print(f"✓ Бэкап: {BACKUP_COG}")
else:
    print("✓ Бэкап уже есть")

# ============================================================
# ШАГ 1 — Поиск и умный отбор сцен
# ============================================================

print(f"\nИщу сцены Sentinel-2 L2A...")

catalog = Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=pc.sign_inplace,
)

search = catalog.search(
    collections=["sentinel-2-l2a"],
    intersects=AOI_GEOJSON,
    datetime=DATE_RANGE,
    query={"eo:cloud_cover": {"lt": MAX_CLOUD}},
)

items = list(search.items())
print(f"Найдено всего сцен: {len(items)}")

# ============================================================
# УМНЫЙ ОТБОР: 1 лучшая сцена на каждый уникальный тайл
# ============================================================

by_tile = defaultdict(list)
for item in items:
    tile_id = item.properties.get("s2:mgrs_tile", "unknown")
    by_tile[tile_id].append(item)

print(f"Уникальных тайлов (MGRS grid): {len(by_tile)}")

# На каждый тайл берём сцену с минимальной облачностью
best_items = []
for tile_id, tile_items in sorted(by_tile.items()):
    tile_items.sort(key=lambda x: x.properties.get("eo:cloud_cover", 100))
    best = tile_items[0]
    cloud = best.properties.get("eo:cloud_cover", "?")
    print(f"  Тайл {tile_id}: {best.id[:40]}... облачность={cloud}%")
    best_items.append(best)

print(f"\n✓ Отобрано сцен: {len(best_items)} (по 1 на каждый тайл)")
print(f"  Экономия: {len(items) - len(best_items)} сцен пропущено")

items = best_items

if len(items) == 0:
    print("❌ Нет сцен!")
    exit(1)

# ============================================================
# ШАГ 2 — Скачать отобранные сцены
# ============================================================

TEMP_DIR.mkdir(parents=True, exist_ok=True)
aoi_shape = shape(AOI_GEOJSON)
band_files = {b: [] for b in BANDS}

print(f"\nСкачиваю {len(items)} сцен...")

for i, item in enumerate(items):
    cloud = item.properties.get("eo:cloud_cover", "?")
    tile_id = item.properties.get("s2:mgrs_tile", "?")
    print(f"\n[{i+1}/{len(items)}] Тайл {tile_id} | облачность {cloud}%")

    scene_dir = TEMP_DIR / item.id
    scene_dir.mkdir(exist_ok=True)

    for band in BANDS:
        out_path = scene_dir / f"{band}.tif"

        if out_path.exists() and out_path.stat().st_size > 10000:
            print(f"  ✓ {band} уже есть")
            band_files[band].append(str(out_path))
            continue

        try:
            href = pc.sign(item.assets[band].href)

            with rasterio.open(href) as src:
                aoi_reproj = gpd.GeoDataFrame(
                    geometry=[aoi_shape], crs="EPSG:4326"
                ).to_crs(src.crs).geometry[0]

                out_data, out_transform = mask(
                    src, [mapping(aoi_reproj)],
                    crop=True, nodata=0
                )

                # Пропустить если пустой тайл
                if np.all(out_data == 0):
                    print(f"  ⚠️  {band} пустой тайл, пропускаю")
                    continue

                out_meta = src.meta.copy()
                out_meta.update({
                    "driver": "GTiff",
                    "height": out_data.shape[1],
                    "width": out_data.shape[2],
                    "transform": out_transform,
                    "nodata": 0,
                    "compress": "deflate",
                })

                with rasterio.open(out_path, "w", **out_meta) as dst:
                    dst.write(out_data)

            print(f"  ✓ {band}")
            band_files[band].append(str(out_path))

        except Exception as e:
            print(f"  ❌ {band}: {e}")

# ============================================================
# ШАГ 3 — Репроекция в EPSG:32641 ПЕРЕД мержем
# ============================================================

print(f"\nРепроецирую все файлы в {TARGET_CRS} перед мержем...")

reproj_dir = TEMP_DIR / "reproj_bands"
reproj_dir.mkdir(exist_ok=True)

band_files_reproj = {b: [] for b in BANDS}

for band in BANDS:
    files = [f for f in band_files[band] if Path(f).exists()]
    if not files:
        print(f"  ⚠️  {band}: нет файлов!")
        continue

    for fpath in files:
        scene_name = Path(fpath).parent.name
        out_reproj = reproj_dir / f"{scene_name}_{band}.tif"

        if out_reproj.exists() and out_reproj.stat().st_size > 1000:
            band_files_reproj[band].append(str(out_reproj))
            continue

        with rasterio.open(fpath) as src:
            if str(src.crs) == TARGET_CRS:
                # Уже правильная CRS — просто копируем путь
                band_files_reproj[band].append(fpath)
                continue

            # Репроецируем
            transform, width, height = calculate_default_transform(
                src.crs, TARGET_CRS,
                src.width, src.height, *src.bounds
            )
            meta = src.meta.copy()
            meta.update({
                "crs": TARGET_CRS,
                "transform": transform,
                "width": width,
                "height": height,
                "compress": "deflate",
                "nodata": 0,
            })
            with rasterio.open(out_reproj, "w", **meta) as dst:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=rasterio.band(dst, 1),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=TARGET_CRS,
                    resampling=Resampling.bilinear,
                )
        band_files_reproj[band].append(str(out_reproj))

    print(f"  ✓ {band}: {len(band_files_reproj[band])} файлов готово")

# ============================================================
# ШАГ 4 — Мерж уже репроецированных файлов
# ============================================================

print(f"\nМержу тайлы (все в {TARGET_CRS})...")

merged_dir = TEMP_DIR / "merged"
merged_dir.mkdir(exist_ok=True)
merged_bands = []

for band in BANDS:
    files = band_files_reproj[band]
    if not files:
        print(f"  ⚠️  {band}: нет файлов!")
        continue

    out_merged = merged_dir / f"{band}_merged.tif"

    if out_merged.exists() and out_merged.stat().st_size > 10000:
        print(f"  ✓ {band} уже смержен")
        merged_bands.append((band, str(out_merged)))
        continue

    print(f"  Мержу {band}: {len(files)} файлов...")

    datasets = []
    for f in files:
        try:
            datasets.append(rasterio.open(f))
        except Exception as e:
            print(f"    ⚠️  Пропускаю {f}: {e}")

    if not datasets:
        print(f"  ❌ {band}: все файлы битые")
        continue

    try:
        mosaic, transform = merge(datasets, method="first", nodata=0)
        meta = datasets[0].meta.copy()
        meta.update({
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": transform,
            "nodata": 0,
            "compress": "deflate",
            "crs": TARGET_CRS,
        })
        with rasterio.open(out_merged, "w", **meta) as dst:
            dst.write(mosaic)
        print(f"  ✓ {band} смержен")
        merged_bands.append((band, str(out_merged)))
    except Exception as e:
        print(f"  ❌ {band} мерж упал: {e}")
    finally:
        for ds in datasets:
            ds.close()

# ============================================================
# ШАГ 5 — Многобэндовый COG
# ============================================================

print(f"\nСобираю финальный COG...")

temp_multiband = TEMP_DIR / "multiband.tif"

with rasterio.open(merged_bands[0][1]) as ref:
    meta = ref.meta.copy()
    meta.update({
        "count": len(merged_bands),
        "driver": "GTiff",
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "nodata": 0,
    })

with rasterio.open(temp_multiband, "w", **meta) as dst:
    for i, (band, path) in enumerate(merged_bands, 1):
        print(f"  Записываю {band} ({i}/{len(merged_bands)})...")
        with rasterio.open(path) as src:
            dst.write(src.read(1), i)

print(f"\nКонвертирую в COG формат...")

import rasterio
from rasterio.shutil import copy as rio_copy

with rasterio.open(temp_multiband) as src:
    rio_copy(
        src,
        OUTPUT_COG,
        driver="GTiff",
        compress="deflate",
        tiled=True,
        blockxsize=512,
        blockysize=512,
        copy_src_overviews=False,
    )

# Добавить overviews (пирамиды для быстрого зума)
print("  Строю overviews...")
with rasterio.open(OUTPUT_COG, "r+") as dst:
    dst.build_overviews([2, 4, 8, 16, 32], Resampling.bilinear)
    dst.update_tags(ns="rio_overview", resampling="bilinear")

# ============================================================
# ГОТОВО
# ============================================================

if Path(OUTPUT_COG).exists():
    size_gb = Path(OUTPUT_COG).stat().st_size / 1e9
    print(f"\n{'='*60}")
    print(f"✓ ГОТОВО!")
    print(f"  Новый COG: {OUTPUT_COG} ({size_gb:.1f} GB)")
    print(f"  Бэкап:     {BACKUP_COG}")
    print(f"\nПоменяй в backend/main.py:")
    print(f'  COG_PATH = r"{OUTPUT_COG}"')
    print(f"{'='*60}")
else:
    print(f"\n❌ COG не создан, проверь ошибки выше")