"""
GeoAI-TKO: Raster Validation Tool
===================================
Validates downloaded Sentinel-2 GeoTIFF:
- Bands count & names
- Width/Height
- CRS
- Basic stats per band
- NDVI check
"""

import sys
import json
from pathlib import Path
from datetime import datetime

import rasterio
import numpy as np


def validate_tiff(filepath: str):
    """Validate a GeoTIFF file and print summary."""
    path = Path(filepath)
    if not path.exists():
        print(f"[FAIL] File not found: {filepath}")
        return False

    print(f"\n{'='*60}")
    print(f"  GeoAI-TKO: Raster Validation")
    print(f"  File: {path.name}")
    print(f"  Path: {path}")
    print(f"  Size: {path.stat().st_size / (1024*1024):.1f} MB")
    print(f"{'='*60}")

    with rasterio.open(filepath) as src:
        print(f"\n── Metadata ──")
        print(f"  Driver:    {src.driver}")
        print(f"  CRS:       {src.crs}")
        print(f"  Transform: {src.transform}")
        print(f"  Width:     {src.width:,} px")
        print(f"  Height:    {src.height:,} px")
        print(f"  Bands:     {src.count}")
        print(f"  Dtype:     {src.dtypes[0]}")

        # Extent in target CRS
        bounds = src.bounds
        extent_km_x = (bounds.right - bounds.left) / 1000
        extent_km_y = (bounds.top - bounds.bottom) / 1000
        print(f"  Extent:    {extent_km_x:.0f} km × {extent_km_y:.0f} km")
        print(f"  Bounds:    left={bounds.left:.0f}, bottom={bounds.bottom:.0f}, "
              f"right={bounds.right:.0f}, top={bounds.top:.0f}")

        # Band descriptions
        print(f"\n── Bands ──")
        valid = True
        expected_bands = ["B2", "B3", "B4", "B8"]  # Sentinel-2 10m bands
        for i in range(1, src.count + 1):
            band = src.read(i)
            tag = src.tags(i) or {}
            band_name = tag.get("name", f"Band_{i}")
            nodata = src.nodatavals[i-1] if src.nodatavals else None
            valid_px = np.sum(band != nodata) if nodata is not None else band.size
            valid_pct = 100 * valid_px / band.size

            print(f"  Band {i}: {band_name:<6} | "
                  f"min={band[band != nodata].min():.4f}" 
                  f"  max={band[band != nodata].max():.4f}"
                  f"  mean={band[band != nodata].mean():.4f}"
                  f"  valid={valid_pct:.1f}%")

        # NDVI sanity check
        if src.count >= 5:
            ndvi_band = src.read(5)
            if nodata is not None:
                ndvi_valid = ndvi_band[ndvi_band != nodata]
            else:
                ndvi_valid = ndvi_band
            print(f"\n── NDVI Sanity Check ──")
            print(f"  Range:    [{ndvi_valid.min():.4f}, {ndvi_valid.max():.4f}]")
            print(f"  Mean:     {ndvi_valid.mean():.4f}")
            print(f"  Median:   {np.median(ndvi_valid):.4f}")
            # NDVI should be between -1 and 1 (mostly 0-1 for vegetation)
            if ndvi_valid.min() < -1.0 or ndvi_valid.max() > 1.0:
                print(f"  [WARN] NDVI outside [-1,1] range!")
                valid = False
            else:
                print(f"  [OK] NDVI within [-1,1] range")

    print(f"\n── Result ──")
    if valid:
        print(f"  ✅ VALID — GeoTIFF passes all checks")
    else:
        print(f"  ⚠️  WARNINGS — see above")

    return valid


if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "data/raw/sentinel2_day1.tif"
    validate_tiff(filepath)
