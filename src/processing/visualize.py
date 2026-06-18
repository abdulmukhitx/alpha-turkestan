from pathlib import Path

import matplotlib.pyplot as plt
import rasterio
import numpy as np

BASE_DIR = Path(__file__).resolve().parents[2]

INPUT_RASTER = BASE_DIR / "data" / "processed" / "ndvi_2023_full.tif"
OUTPUT_IMAGE = BASE_DIR / "data" / "processed" / "preview" / "ndvi_2023_preview_full.png"

with rasterio.open(INPUT_RASTER) as src:
    ndvi = src.read(1)

# Заменяем некорректные значения
ndvi = np.ma.masked_invalid(ndvi)

plt.figure(figsize=(12, 10))

plt.imshow(
    ndvi,
    cmap="RdYlGn",
    vmin=-1,
    vmax=1
)

plt.colorbar(label="NDVI")
plt.title("Turkistan Region - NDVI (Summer 2023)")
plt.axis("off")

plt.savefig(
    OUTPUT_IMAGE,
    dpi=300,
    bbox_inches="tight"
)

plt.close()

print(f"Image saved: {OUTPUT_IMAGE}")