from pathlib import Path
import numpy as np 
import rasterio

BASE_DIR= Path(__file__).resolve().parents[2]
INPUT_RASTER= BASE_DIR / "data" / "raw" / "sentinel2_day1.tif"
OUTPUT_RASTER= BASE_DIR / "data" / "processed" / "ndwi_2023.tif"
with rasterio.open (INPUT_RASTER)as src:
    desc= src.descriptions
    band_idx= {name: i+1 for i, name in enumerate(desc) }
    green= src.read(band_idx['B3']).astype("float32")
    nir= src.read(band_idx['B8']).astype("float32")
    profile = src.profile.copy()

ndwi= (green-nir)/(green+nir)
print(f"min={np.nanmin(ndwi):.3f}  max={np.nanmax(ndwi):.3f}  mean={np.nanmean(ndwi):.3f}  nan%={np.isnan(ndwi).mean()*100:.1f}")

profile.update(
    dtype='float32',
    count=1,
    nodata=np.nan
)

with rasterio.open(OUTPUT_RASTER, 'w', **profile) as dst:
    dst.write(ndwi, 1)

print(f"Saved: {OUTPUT_RASTER}")
