import rasterio
from rasterio.warp import transform_bounds
import numpy as np

COG_PATH = r"D:\data\mosaics\2023_summer\s2_mosaic_cog.tif"

with rasterio.open(COG_PATH) as src:
    print(f"CRS: {src.crs}")
    print(f"Размер: {src.width} x {src.height}")
    print(f"Nodata: {src.nodata}")
    print(f"Compression: {src.compression}")
    print(f"Block size: {src.block_shapes}")
    
    bounds_wgs84 = transform_bounds(src.crs, 'EPSG:4326', *src.bounds)
    print(f"\nBounds WGS84:")
    print(f"  West:  {bounds_wgs84[0]:.4f}")
    print(f"  South: {bounds_wgs84[1]:.4f}")
    print(f"  East:  {bounds_wgs84[2]:.4f}")
    print(f"  North: {bounds_wgs84[3]:.4f}")
    
    # Проверка артефактов
    data = src.read(1, window=rasterio.windows.Window(0, 0, 1024, 1024))
    zeros = np.sum(data == 0)
    total = data.size
    print(f"\nНулевые пиксели: {zeros}/{total} ({zeros/total*100:.1f}%)")