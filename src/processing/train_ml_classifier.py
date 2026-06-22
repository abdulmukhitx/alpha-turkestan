"""
GeoAI-TKO · src/processing/train_ml_classifier.py
Обучает Random Forest на ground truth (OSM или синтетика) + NDVI.
"""
import geopandas as gpd, numpy as np, rasterio, json, pickle
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

BASE = Path(__file__).resolve().parents[2]
BOUNDARY = BASE / "data/raw/turkestan_boundary.geojson"
NDVI_PATH = BASE / "data/processed/ndvi_tko_final.tif"
OUT_CLF = BASE / "data/models/rf_classifier.pkl"
OUT_RASTER = BASE / "data/processed/landcover_rf.tif"
OUT_REPORT = BASE / "data/metadata/rf_report.json"

CLASS_NAMES = {0:'Поля',1:'Лес',2:'Пастбища',3:'Вода',4:'Пустыня',5:'Застройка'}
LANDUSE_MAP = {'farmland':0,'cropland':0,'orchard':0,'forest':1,'tree':1,'grassland':2,
    'meadow':2,'pasture':2,'water':3,'reservoir':3,'barren':4,'scrub':4,'sand':4,
    'urban':5,'residential':5,'industrial':5}

print("="*60,"\n  GeoAI-TKO · ML Classifier (Random Forest)\n","="*60)

# ── 1. Ground truth ──
gdf_boundary = gpd.read_file(BOUNDARY)
polygon = gdf_boundary.geometry.iloc[0]

print("\n[1/4] Getting ground truth...")
gdf_osm = None
try:
    import osmnx as ox
    ox.settings.log_console = False
    gdf_osm = ox.features_from_polygon(polygon, {'landuse': True})
    relevant = set(LANDUSE_MAP.keys())
    if 'landuse' in gdf_osm.columns:
        gdf_osm = gdf_osm[gdf_osm['landuse'].astype(str).str.lower().isin(relevant)]
    print(f"  OSM: {len(gdf_osm)} features")
except Exception as e:
    print(f"  OSM: {e}")

if gdf_osm is None or len(gdf_osm) < 50:
    print("  Using synthetic data (OSM unavailable)...")
    from shapely.geometry import Point
    np.random.seed(42)
    bounds = polygon.bounds
    classes_w = [('farmland',0.28),('grassland',0.20),('barren',0.30),
                 ('forest',0.07),('water',0.05),('urban',0.10)]
    pts, cls = [], []
    for name, w in classes_w:
        for _ in range(int(500*w)):
            for _ in range(100):
                x = np.random.uniform(*bounds[::2])
                y = np.random.uniform(*bounds[1::2])
                pt = Point(x,y)
                if polygon.contains(pt):
                    pts.append(pt); cls.append(name); break
    gdf_osm = gpd.GeoDataFrame({'landuse':cls,'geometry':pts}, crs='EPSG:4326')
    print(f"  Synthetic: {len(gdf_osm)} points")

# ── 2. Сэмплирование ──
print("[2/4] Sampling NDVI + training...")
from pyproj import Transformer
t = Transformer.from_crs("EPSG:4326", "EPSG:32642", always_xy=True)
X, y = [], []
with rasterio.open(NDVI_PATH) as src:
    for _, row in gdf_osm.iterrows():
        geom = row.geometry
        pt = geom.centroid if geom.geom_type != 'Point' else geom
        lbl = LANDUSE_MAP.get(str(row.get('landuse','')).lower().strip())
        if lbl is None: continue
        xm, ym = t.transform(pt.x, pt.y)
        r, c = src.index(xm, ym)
        if 0<=r<src.height and 0<=c<src.width:
            v = src.read(1, window=((r,r+1),(c,c+1)))[0,0]
            if not np.isnan(v):
                X.append([v, r, c]); y.append(lbl)

X = np.array(X); y = np.array(y)
print(f"  Samples: {len(y)} classes: {len(set(y))}")
for lbl in set(y):
    print(f"    {CLASS_NAMES[lbl]}: {(y==lbl).sum()}")

if len(set(y)) < 2:
    print("  ERROR: need >=2 classes. Exit."); exit(1)

# ── 3. Train ──
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.3, random_state=42)
rf = RandomForestClassifier(n_estimators=100, max_depth=12, random_state=42, n_jobs=-1)
rf.fit(X_train, y_train)
acc = accuracy_score(y_test, rf.predict(X_test))
print(f"\n  Test accuracy: {acc*100:.1f}%")
print("  Report:")
rep = classification_report(y_test, rf.predict(X_test),
    target_names=[CLASS_NAMES[i] for i in sorted(CLASS_NAMES)],
    zero_division=0, output_dict=True)
print(classification_report(y_test, rf.predict(X_test),
    target_names=[CLASS_NAMES[i] for i in sorted(CLASS_NAMES)], zero_division=0))

# ── 4. Apply to full raster ──
print("[4/4] Applying to full raster...")
with rasterio.open(NDVI_PATH) as src:
    profile = src.profile.copy()
    profile.update(dtype=np.uint8, nodata=255, compress='lzw', count=1)
    with rasterio.open(OUT_RASTER, 'w', **profile) as dst:
        BLOCK = 2048; h, w = src.height, src.width
        for r_off in range(0, h, BLOCK):
            bh = min(BLOCK, h - r_off)
            for c_off in range(0, w, BLOCK):
                bw = min(BLOCK, w - c_off)
                win = rasterio.windows.Window(c_off, r_off, bw, bh)
                data = src.read(1, window=win)
                if hasattr(data,'mask'): data = data.filled(np.nan)
                out = np.full(data.shape, 255, np.uint8)
                valid = ~np.isnan(data)
                if valid.any():
                    ri, ci = np.mgrid[r_off:r_off+bh, c_off:c_off+bw]
                    feats = np.column_stack([data.ravel(), ri.ravel(), ci.ravel()])
                    out.ravel()[valid.ravel()] = rf.predict(feats[valid.ravel()])
                dst.write(out, 1, window=win)
            print(f"\r  {min(r_off+bh,h)}/{h}", end="", flush=True)
    print()

# Save
Path("data/models").mkdir(parents=True, exist_ok=True)
with open(OUT_CLF, 'wb') as f:
    pickle.dump({'model':rf,'classes':CLASS_NAMES,'accuracy':acc}, f)
with open(OUT_REPORT, 'w', encoding='utf-8') as f:
    json.dump(rep, f, indent=2, ensure_ascii=False, default=str)

print(f"\n{'='*60}\n  DONE. RF accuracy: {acc*100:.1f}%\n  Model: {OUT_CLF}\n{'='*60}")
