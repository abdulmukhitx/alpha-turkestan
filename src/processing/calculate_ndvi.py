from pathlib import Path

import numpy as np
import rasterio


BASE_DIR= Path(__file__).resolve().parents[2]
INPUT_RASTER= BASE_DIR / "data" / "raw" / "full_tiles" / "sentinel2_day1_full.tif"
OUTPUT_RASTER = BASE_DIR / "data" / "processed" / "ndvi_2023_full.tif"

with rasterio.open (INPUT_RASTER) as src:
    red = src.read(3).astype('float32')
    nir = src.read(4).astype('float32')
    profile = src.profile.copy()


ndvi=(nir-red)/(nir+red)

print(ndvi)
print(f"min={np.nanmin(ndvi):.3f}  max={np.nanmax(ndvi):.3f}  mean={np.nanmean(ndvi):.3f}  nan%={np.isnan(ndvi).mean()*100:.1f}")




profile.update(
    dtype='float32',
    count=1,
    nodata=np.nan
)

with rasterio.open(OUTPUT_RASTER, 'w', **profile) as dst:
    dst.write(ndvi, 1)

print(f"Saved: {OUTPUT_RASTER}")