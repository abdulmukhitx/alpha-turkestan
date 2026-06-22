"""
GeoAI-TKO · src/processing/landcover_classify.py
Блочная классификация землепользования по NDVI + NDWI.
"""
import rasterio, numpy as np
from pathlib import Path
from rasterio.windows import Window

BASE = Path(__file__).resolve().parents[2]
NDVI_PATH = BASE / "data/processed/ndvi_tko_final.tif"
NDWI_PATH = BASE / "data/processed/ndwi_tko_final.tif"
OUT_PATH   = BASE / "data/processed/landcover_tko.tif"
STATS_PATH = BASE / "data/metadata/landcover_stats.json"

# Классы
CLASSES = {
    0: "NoData",
    1: "Вода",
    2: "Густая растительность",
    3: "Поля / ирригация",
    4: "Пастбища",
    5: "Разреженная растительность",
    6: "Голая почва",
    7: "Солончак / barren",
}
COLORS = {0:(0,0,0,0),1:(38,189,248,255),2:(0,104,55,255),3:(0,255,135,255),
          4:(126,203,53,255),5:(160,140,100,255),6:(180,160,130,255),7:(220,200,180,255)}

def classify(ndvi, nodata):
    """Поникельная классификация только по NDVI."""
    mask = (ndvi == nodata) | np.isnan(ndvi)
    lc = np.zeros_like(ndvi, dtype=np.uint8)
    # Вода / barren (NDVI < 0)
    lc[(ndvi < 0) & ~mask] = 1
    # Густая растительность
    lc[(ndvi > 0.5) & ~mask] = 2
    # Поля/ирригация
    lc[(ndvi > 0.3) & (ndvi <= 0.5) & ~mask] = 3
    # Пастбища
    lc[(ndvi > 0.15) & (ndvi <= 0.3) & ~mask] = 4
    # Разреженная растительность
    lc[(ndvi > 0.05) & (ndvi <= 0.15) & ~mask] = 5
    # Голая почва
    lc[(ndvi >= 0) & (ndvi <= 0.05) & ~mask] = 6
    return lc

BLOCK = 2048
if __name__ == "__main__":
    import json, time
    t0 = time.time()

    with rasterio.open(NDVI_PATH) as src_ndvi:

        profile = src_ndvi.profile.copy()
        profile.update(dtype=np.uint8, nodata=0, compress='lzw',
                       count=1, bigtiff='YES')

        with rasterio.open(OUT_PATH, 'w', **profile) as dst:
            for row_off in range(0, src_ndvi.height, BLOCK):
                h = min(BLOCK, src_ndvi.height - row_off)
                for col_off in range(0, src_ndvi.width, BLOCK):
                    w = min(BLOCK, src_ndvi.width - col_off)
                    win = Window(col_off, row_off, w, h)

                    ndvi = src_ndvi.read(1, window=win)
                    if hasattr(ndvi, 'mask'):
                        ndvi = ndvi.filled(src_ndvi.nodata or np.nan)

                    lc = classify(ndvi, src_ndvi.nodata or -999)
                    dst.write(lc, 1, window=win)

                pct = (row_off + h) / src_ndvi.height * 100
                print(f"\rClassify: {pct:.0f}%", end="", flush=True)

    # Статистика
    with rasterio.open(OUT_PATH) as src:
        data = src.read(1)
        total = (data > 0).sum()
        stats = {}
        for k, name in CLASSES.items():
            cnt = (data == k).sum()
            if k > 0 and cnt > 0:
                stats[name] = {"pixels": int(cnt), "pct": round(cnt/total*100, 1)}
        with open(STATS_PATH, 'w') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s. Saved: {OUT_PATH.name}, stats: {STATS_PATH.name}")
    for name, s in stats.items():
        print(f"  {name}: {s['pct']}%")
