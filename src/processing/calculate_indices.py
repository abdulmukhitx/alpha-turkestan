"""Индексы из STAC тайла: NDBI, MNDWI, SAVI.
Обрезаем NDVI/NDWI/B8 под геометрию B11."""
import rasterio, numpy as np, json
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DIR = BASE / "data" / "processed"
OUT = BASE / "data" / "processed"

PATHS = {
    "ndvi": DIR / "ndvi_tko_final.tif",
    "ndwi": DIR / "ndwi_tko_final.tif",
    "b8":   DIR / "B8_20m_2023.tif",
    "b11":  DIR / "B11_2023.tif",
}
OUTPUTS = {
    "ndbi": OUT / "ndbi_tko.tif",
    "mndwi": OUT / "mndwi_tko.tif",
    "savi": OUT / "savi_tko.tif",
}

L = 0.5  # soil brightness for SAVI

print("=" * 60)
print("  Индексы: NDBI, MNDWI, SAVI")
print("=" * 60)

with rasterio.open(PATHS["b11"]) as b11:
    # Читаем бенды, окропленные под B11
    def read_to_b11(path, fill_nan=True):
        with rasterio.open(path) as src:
            # Окропить под bounds B11
            window = src.window(*b11.bounds).round_offsets().round_lengths()
            data = src.read(1, window=window, boundless=True)
            if fill_nan and hasattr(data, 'mask'):
                data = data.filled(np.nan)
            return data, src.transform, src.crs

    print("\nReading and cropping to B11 geometry...")
    b11_arr = b11.read(1).astype(np.float32)
    if hasattr(b11_arr, 'mask'): b11_arr = b11_arr.filled(np.nan)

    ndvi, _, _ = read_to_b11(PATHS["ndvi"])
    ndwi, _, _ = read_to_b11(PATHS["ndwi"])
    b8, b8_tf, _ = read_to_b11(PATHS["b8"])

    h, w = b11.height, b11.width
    # Resample all to 20m (B11 size)
    import rasterio.warp
    ndvi_20 = ndvi if ndvi.shape == (h, w) else np.full((h,w), np.nan)
    ndwi_20 = ndwi if ndwi.shape == (h, w) else np.full((h,w), np.nan)
    b8_20m  = b8  if b8.shape  == (h, w) else np.full((h,w), np.nan)

    if ndvi.shape != (h, w):
        from scipy.ndimage import zoom
        zy = h / ndvi.shape[0] if ndvi.shape[0] > 0 else 1
        zx = w / ndvi.shape[1] if ndvi.shape[1] > 0 else 1
        if zy > 0 and zx > 0:
            ndvi_20 = zoom(ndvi.astype(np.float32), (zy, zx), order=1)
            ndwi_20 = zoom(ndwi.astype(np.float32), (zy, zx), order=1) if ndwi.shape == ndvi.shape else ndwi_20
    # B8
    if b8.shape != (h, w):
        zy = h / b8.shape[0] if b8.shape[0] > 0 else 1
        zx = w / b8.shape[1] if b8.shape[1] > 0 else 1
        if zy > 0 and zx > 0:
            b8_20m = zoom(b8.astype(np.float32), (zy, zx), order=1)

    # ── NDBI ──
    print("\nNDBI = (B11 - B8) / (B11 + B8)...")
    mask = (b11_arr + b8_20m) > 0
    ndbi = np.full_like(b11_arr, np.nan)
    ndbi[mask] = (b11_arr[mask] - b8_20m[mask]) / (b11_arr[mask] + b8_20m[mask])
    print(f"  min={np.nanmin(ndbi):.3f} max={np.nanmax(ndbi):.3f} mean={np.nanmean(ndbi):.4f}")

    # MNDWI = (Green - SWIR) / (Green + SWIR)
    # Green из NDWI: NDWI = (B3 - B8)/(B3 + B8) → B3 = B8*(NDWI+1)/(1-NDWI)
    print("\nMNDWI: restoring B3 from NDWI...")
    # Clamp NDWI to avoid division issues
    ndwi_fixed = np.clip(ndwi_20, -0.99, 0.99)
    adj = (1 - ndwi_fixed)
    mask_b3 = ~np.isnan(ndwi_fixed) & ~np.isnan(b8_20m) & (np.abs(adj) > 0.001)
    b3_20m = np.full_like(b8_20m, np.nan)
    b3_20m[mask_b3] = b8_20m[mask_b3] * (ndwi_fixed[mask_b3] + 1) / adj[mask_b3]
    # Fallback where B3 isn't recoverable
    if np.all(np.isnan(b3_20m)):
        print("  B3 restoration failed — using B8 * 0.3 as fallback")
        b3_20m = b8_20m * 0.3

    denom = b3_20m + b11_arr
    mask_m = (denom > 0) & ~np.isnan(b3_20m)
    mndwi = np.full_like(b11_arr, np.nan)
    mndwi[mask_m] = (b3_20m[mask_m] - b11_arr[mask_m]) / denom[mask_m]
    print(f"  min={np.nanmin(mndwi):.3f} max={np.nanmax(mndwi):.3f} mean={np.nanmean(mndwi):.4f}")

    # ── Сохранить NDBI + MNDWI ──
    profile = b11.profile.copy()
    profile.update(dtype=np.float32, count=1, compress='lzw', nodata=np.nan)
    for name, arr in [("ndbi", ndbi), ("mndwi", mndwi)]:
        p = OUTPUTS[name]; p.parent.mkdir(exist_ok=True)
        with rasterio.open(p, 'w', **profile) as dst:
            dst.write(arr.astype(np.float32), 1)
        print(f"  Saved: {p.name}")

# ── SAVI на 10м ──
print("\nSAVI (10m): restoring B4 from NDVI + B8...")
with rasterio.open(PATHS["b8"]) as b8_src, \
     rasterio.open(PATHS["ndvi"]) as ndvi_src:
    # Найти пересечение bbox B8 и NDVI
    from shapely.geometry import box as sbox
    b8_box = sbox(*b8_src.bounds)
    ndvi_box = sbox(*ndvi_src.bounds)
    inter = b8_box.intersection(ndvi_box)
    if inter.is_empty:
        print("  ERROR: B8 tile не пересекается с NDVI!"); exit(1)
    
    # Окно NDVI по пересечению
    win_ndvi = ndvi_src.window(*inter.bounds)
    ndvi10 = ndvi_src.read(1, window=win_ndvi).astype(np.float32)
    
    # Окно B8 по пересечению
    win_b8 = b8_src.window(*inter.bounds)
    b8_10m = b8_src.read(1, window=win_b8).astype(np.float32)
    
    def safe_unmask(a):
        if hasattr(a, 'mask'): return a.filled(np.nan)
        return a.astype(np.float32)
    ndvi10 = safe_unmask(ndvi10)
    b8_10m = safe_unmask(b8_10m)
    
    print(f"  NDVI valid: {np.sum(~np.isnan(ndvi10)):,} / {ndvi10.size:,}")
    print(f"  B8 valid: {np.sum(~np.isnan(b8_10m)):,} / {b8_10m.size:,}")

    # B4 = B8 * (1 - NDVI) / (1 + NDVI)
    denom = 1 + ndvi10
    mask = ~np.isnan(ndvi10) & ~np.isnan(b8_10m) & (np.abs(denom) > 0.001)
    b4 = np.full_like(b8_10m, np.nan)
    b4[mask] = b8_10m[mask] * (1 - ndvi10[mask]) / denom[mask]
    print(f"  B4 valid: {np.sum(~np.isnan(b4)):,}")

    # SAVI = (B8 - B4) / (B8 + B4 + L) * (1+L)
    L = 0.5
    denom_s = b8_10m + b4 + L
    valid = (denom_s > 0) & ~np.isnan(b4) & ~np.isnan(b8_10m)
    savi = np.full_like(b8_10m, np.nan)
    savi[valid] = (b8_10m[valid] - b4[valid]) / denom_s[valid] * (1 + L)
    print(f"  SAVI valid: {np.sum(~np.isnan(savi)):,}")
    print(f"  SAVI: min={np.nanmin(savi):.4f} max={np.nanmax(savi):.4f} mean={np.nanmean(savi):.4f}")

    profile_savi = b8_src.profile.copy()
    profile_savi.update(dtype=np.float32, count=1, compress='lzw', nodata=np.nan)
    with rasterio.open(OUTPUTS["savi"], 'w', **profile_savi) as dst:
        dst.write(savi.astype(np.float32), 1)
    print(f"  Saved: {OUTPUTS['savi'].name}")

# ── Статистика и metadata ──
stats = {}
for name, path in OUTPUTS.items():
    with rasterio.open(path) as src:
        d = src.read(1)
        stats[name] = {
            "mean": round(float(np.nanmean(d)), 4) if not np.all(np.isnan(d)) else None,
            "min": round(float(np.nanmin(d)), 4) if not np.all(np.isnan(d)) else None,
            "max": round(float(np.nanmax(d)), 4) if not np.all(np.isnan(d)) else None,
            "size": f"{src.width}x{src.height}",
            "crs": str(src.crs),
        }
    print(f"\n{name.upper()}: {stats[name]}")

(OUT / "metadata").mkdir(parents=True, exist_ok=True)
with open(OUT / "metadata" / "indices_stats.json", 'w', encoding='utf-8') as f:
    json.dump(stats, f, indent=2, ensure_ascii=False)

print(f"\n{'='*60}")
print(f"  Done.")
print(f"{'='*60}")
