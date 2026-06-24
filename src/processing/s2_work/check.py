from pathlib import Path
import rasterio

count = 0

for f in Path(".").glob("*.tif"):
    try:
        with rasterio.open(f) as ds:
            print("OK:", f.name, ds.width, ds.height, ds.count)
            count += 1
    except Exception as e:
        print("BROKEN:", f.name, e)

print("TOTAL OK:", count)