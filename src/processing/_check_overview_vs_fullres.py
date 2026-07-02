import json
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
from rasterio.windows import from_bounds
from rasterio.enums import Resampling

COG_PATH = Path(r"D:\data\mosaics\2025_summer\s2_mosaic_cog.tif")
MANIFEST_PATH = Path(r"D:\data\s2_2025_raw\manifest.json")
manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

with rasterio.open(COG_PATH) as ds:
    print("Overview factors available for band 1:", ds.overviews(1))
    nodata = ds.nodata

    primary_b02 = manifest["tiles"]["42TVN"]["bands"]["B02"]
    with rasterio.open(primary_b02) as ref:
        bounds_main_crs = rasterio.warp.transform_bounds(ref.crs, ds.crs, *ref.bounds)
    win = from_bounds(*bounds_main_crs, transform=ds.transform).round_offsets().round_lengths()
    col_off = max(0, int(win.col_off)); row_off = max(0, int(win.row_off))
    col_end = min(ds.width, int(win.col_off)+int(win.width)); row_end = min(ds.height, int(win.row_off)+int(win.height))
    w = col_end-col_off; h = row_end-row_off
    win = rasterio.windows.Window(col_off, row_off, w, h)

    # Force reading via the coarsest overview explicitly (nearest, matches build)
    factor = max(ds.overviews(1))
    out_h = max(1, h // factor)
    out_w = max(1, w // factor)
    ov_data = ds.read(1, window=win, out_shape=(out_h, out_w), resampling=Resampling.nearest)
    n_nodata_ov = int(np.sum(ov_data == nodata))
    print(f"42TVN via coarsest overview (factor={factor}, nearest): {n_nodata_ov}/{ov_data.size} = "
          f"{100*n_nodata_ov/ov_data.size:.2f}% nodata")

    # also try default resampling (no explicit arg) to see if that's the actual code path check_4 used
    ov_data_default = ds.read(1, window=win, out_shape=(out_h, out_w))
    n_nodata_def = int(np.sum(ov_data_default == nodata))
    print(f"42TVN via coarsest overview (factor={factor}, default resampling arg): "
          f"{n_nodata_def}/{ov_data_default.size} = {100*n_nodata_def/ov_data_default.size:.2f}% nodata")

    # check without window (whole-raster out_shape like check_4_preview does)
    full_ovr = ds.overviews(1)
    full_factor = max(full_ovr) if full_ovr else 1
    full_out_h = max(1, ds.height // full_factor)
    full_out_w = max(1, ds.width // full_factor)
    print(f"\nfull raster preview-style read: factor={full_factor}, out_shape=({full_out_h},{full_out_w})")
    whole = ds.read(1, out_shape=(full_out_h, full_out_w))
    print(f"whole-raster overview read: {int(np.sum(whole==nodata))}/{whole.size} = "
          f"{100*np.sum(whole==nodata)/whole.size:.2f}% nodata total")
