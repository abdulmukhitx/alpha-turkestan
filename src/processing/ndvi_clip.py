"""
NDVI + Turkestan boundary mask — поблочная обработка.
Использует raster_utils, формулу из calculate_ndvi, подход из ndvi_clip.
"""
import sys
from pathlib import Path

# Добавляем src/processing в путь
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'processing'))

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.features import geometry_mask
from raster_utils import save_single_band

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ── Пути ──
RASTER = Path(r'C:\Users\oqa1a\GeoAI-TKO\data\raw\sentinel2_day1_full.tif')
OUT_NDVI = Path(r'C:\Users\oqa1a\GeoAI-TKO\data\processed\ndvi_2023_full.tif')
OUT_PNG = Path(r'C:\Users\oqa1a\GeoAI-TKO\data\raw\ndvi_turkestan.png')
OUT_PNG.parent.mkdir(parents=True, exist_ok=True)

# ── 1. Граница Туркестанской области ──
print("Loading boundary...")
kz = gpd.read_file('https://geodata.ucdavis.edu/gadm/gadm4.1/gpkg/gadm41_KAZ.gpkg', layer='ADM_ADM_1')
turkestan = kz[kz['NAME_1'] == 'South Kazakhstan'].copy()
print(f"Turkestan: {turkestan['NAME_1'].iloc[0]}")

# ── 2. NDVI поблочно + маска ──
print("Computing NDVI block-wise...")
with rasterio.open(RASTER) as src:
    turkestan_raster_crs = turkestan.to_crs(src.crs)

    # Создаём маску границы
    shapes = [geom for geom in turkestan_raster_crs.geometry]
    mask_arr = geometry_mask(shapes, transform=src.transform,
                             invert=True, out_shape=(src.height, src.width))

    # NDVI поблочно (формула из calculate_ndvi.py)
    ndvi = np.full((src.height, src.width), np.nan, dtype='float32')
    bs = 4096  # размер блока
    for r in range(0, src.height, bs):
        h = min(bs, src.height - r)
        for c in range(0, src.width, bs):
            w = min(bs, src.width - c)
            win = ((r, r+h), (c, c+w))
            red = src.read(3, window=win).astype('float32')
            nir = src.read(4, window=win).astype('float32')
            denom = nir + red
            valid = denom > 0
            res = np.full((h, w), np.nan, dtype='float32')
            res[valid] = (nir[valid] - red[valid]) / denom[valid]
            ndvi[r:r+h, c:c+w] = res
        print(f"  row {r}/{src.height}", flush=True)

    # Применяем маску
    ndvi_masked = np.where(mask_arr, ndvi, np.nan)

    # Статистика
    valid = ndvi_masked[~np.isnan(ndvi_masked)]
    print(f"NDVI: min={np.nanmin(valid):.3f}  max={np.nanmax(valid):.3f}  mean={np.nanmean(valid):.3f}")
    veg = (ndvi_masked > 0.3).sum()
    dense = (ndvi_masked > 0.6).sum()
    total_valid = (~np.isnan(ndvi_masked)).sum()
    print(f"Vegetation >0.3: {100*veg/total_valid:.1f}%")
    print(f"Dense     >0.6: {100*dense/total_valid:.1f}%")

    # Запоминаем bounds для графика
    raster_bounds = src.bounds
    raster_crs = src.crs

    # ── 3. Сохраняем NDVI GeoTIFF через raster_utils ──
    profile = src.profile.copy()
    profile.update(nodata=np.nan)
    save_single_band(OUT_NDVI, ndvi_masked, profile)
    print(f"Saved: {OUT_NDVI}")

# ── 4. График (с даунсэмплом и правильными координатами) ──
print("Plotting...")
f = max(1, max(ndvi_masked.shape) // 2000)
if f > 1:
    h2, w2 = ndvi_masked.shape[0]//f, ndvi_masked.shape[1]//f
    trimmed = ndvi_masked[:h2*f, :w2*f]
    reshaped = trimmed.reshape(h2, f, w2, f)
    with np.errstate(all='ignore'):
        ndvi_small = np.nanmean(reshaped, axis=(1,3))
else:
    ndvi_small = ndvi_masked

extent = [raster_bounds.left, raster_bounds.right, raster_bounds.bottom, raster_bounds.top]

fig, ax = plt.subplots(figsize=(16, 14), dpi=150)
im = ax.imshow(ndvi_small, cmap='RdYlGn', vmin=-0.2, vmax=0.9,
               interpolation='bilinear', extent=extent)
turkestan_raster_crs.boundary.plot(ax=ax, color='black', linewidth=1.2)
plt.colorbar(im, ax=ax, label='NDVI', shrink=0.75)
ax.set_title('NDVI — Туркестанская область\n(Sentinel-2, медианный композит, лето 2023)', fontsize=13)
ax.set_xlabel('UTM Easting (м)'); ax.set_ylabel('UTM Northing (м)')
ax.set_aspect('equal')
fig.tight_layout()
fig.savefig(OUT_PNG, bbox_inches='tight', facecolor='white')
print(f"Saved: {OUT_PNG}")
print("✅ Готово")
