"""
GeoAI-TKO · src/processing/zonal_stats.py
Зональная статистика NDVI по Туркестанской области + сетка.
"""
import rasterio, numpy as np, json, geopandas as gpd
from pathlib import Path
from rasterio.features import geometry_mask
from shapely.geometry import shape

BASE = Path(__file__).resolve().parents[2]
NDVI_PATH = BASE / "data/processed/ndvi_tko_final.tif"
BOUNDARY  = BASE / "data/raw/turkestan_boundary.geojson"
OUT_JSON  = BASE / "data/metadata/zonal_stats.json"

if __name__ == "__main__":
    with open(BOUNDARY) as f:
        geom = shape(json.load(f)["features"][0]["geometry"])

    with rasterio.open(NDVI_PATH) as src:
        # Bounds области в CRS растра
        from rasterio.warp import transform_geom
        geom_proj = transform_geom("EPSG:4326", src.crs, geom.__geo_interface__)

        # Читаем окно по bounds области с запасом
        bounds = shape(geom_proj).bounds
        window = src.window(*bounds).round_offsets().round_lengths()
        window = window.intersection(rasterio.windows.Window(0, 0, src.width, src.height))

        data = src.read(1, window=window)
        if hasattr(data, 'mask'):
            data = data.filled(np.nan)

        # Маска GADM
        mask = geometry_mask([geom_proj], out_shape=data.shape,
                             transform=src.window_transform(window), invert=True)

        valid = data[mask & ~np.isnan(data)]
        total_area_km2 = mask.sum() * 100 / 1e6  # 10m pixels → km²

        stats = {
            "region": "Туркестанская область",
            "area_km2": round(total_area_km2, 0),
            "ndvi": {
                "mean": round(float(np.mean(valid)), 4),
                "std":  round(float(np.std(valid)), 4),
                "min":  round(float(np.min(valid)), 4),
                "max":  round(float(np.max(valid)), 4),
                "median": round(float(np.median(valid)), 4),
            },
            "pixel_count": int(mask.sum()),
            "valid_pixels": int(len(valid)),
            "no_data_pct": round((mask.sum() - len(valid)) / mask.sum() * 100, 2),
        }
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    with open(OUT_JSON, 'w') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"Saved: {OUT_JSON}")
