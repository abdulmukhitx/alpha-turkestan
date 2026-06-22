"""
GeoAI-TKO · ML Day 6 — Визуализация результатов
Запуск: python notebooks/ml_results_viz.py
Создаёт PNG в data/outputs/
"""
import rasterio, numpy as np, json, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path
from rasterio.windows import Window

BASE = Path(__file__).resolve().parents[1] / "data"
OUT = BASE / "outputs"
OUT.mkdir(exist_ok=True)

# Тёмная тема
matplotlib.rcParams.update({
    'figure.facecolor': '#1a1a2e', 'axes.facecolor': '#0d1117',
    'text.color': 'white', 'axes.edgecolor': '#30363d',
    'axes.labelcolor': 'white', 'xtick.color': '#8b949e', 'ytick.color': '#8b949e',
    'figure.dpi': 120, 'savefig.dpi': 150, 'savefig.bbox': 'tight',
    'savefig.facecolor': '#1a1a2e'
})

# ====================== 1. Зональная статистика ======================
with open(BASE / 'metadata/zonal_stats.json') as f:
    stats = json.load(f)
ndvi = stats['ndvi']

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
metrics = ['mean', 'median', 'std']; vals = [ndvi[m] for m in metrics]
bars = ax1.bar(metrics, vals, color=['#00FF87', '#38BDF8', '#FFB703'])
ax1.set_title('NDVI — метрики по области'); ax1.set_ylabel('Значение')
for b, v in zip(bars, vals):
    ax1.text(b.get_x()+b.get_width()/2, b.get_height()+0.005, f'{v:.4f}', ha='center', fontsize=12)

ax2.barh(['min', 'max'], [ndvi['min'], ndvi['max']], color=['#FF4444', '#00FF87'])
ax2.set_title(f"NDVI — диапазон")
for i, (v, label) in enumerate(zip([ndvi['min'], ndvi['max']], ['min', 'max'])):
    ax2.text(v+0.02, i, f'{v:.4f}', va='center', fontsize=10)
plt.tight_layout(); plt.savefig(OUT / 'zonal_stats.png'); plt.close()
print(f"1/4 zonal_stats.png")

# ====================== 2. Классификация ======================
with open(BASE / 'metadata/landcover_stats.json') as f:
    lc_stats = json.load(f)

CLASS_COLORS = {
    'Вода': '#2196F3', 'Густая растительность': '#006837',
    'Поля / ирригация': '#00C853', 'Пастбища': '#8BC34A',
    'Разреженная растительность': '#CDDC39', 'Голая почва': '#D7CCC8',
}
names = list(lc_stats.keys())
pcts = [lc_stats[n]['pct'] for n in names]
colors = [CLASS_COLORS.get(n, '#gray') for n in names]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
bars = ax1.barh(names, pcts, color=colors)
ax1.set_xlabel('% площади'); ax1.set_title('Землепользование')
ax1.invert_yaxis()
for b, p in zip(bars, pcts):
    ax1.text(b.get_width()+0.3, b.get_y()+b.get_height()/2, f'{p}%', va='center')

wedges, texts, autotexts = ax2.pie(pcts, labels=None, autopct='%1.1f%%', colors=colors,
                                     textprops={'fontsize': 8}, pctdistance=0.82)
ax2.legend(wedges, names, loc='center left', bbox_to_anchor=(1, 0.5), fontsize=8)
ax2.set_title('Доля классов')
plt.tight_layout(); plt.savefig(OUT / 'landcover_charts.png'); plt.close()
print(f"2/4 landcover_charts.png")

# ====================== 3. Карта классификации ======================
LC_PATH = BASE / 'processed/landcover_tko.tif'
NDVI_PATH = BASE / 'processed/ndvi_tko_final.tif'

LC_NAMES = {0:'NoData',1:'Вода',2:'Густая раст.',3:'Поля',4:'Пастбища',5:'Разреж.раст.',6:'Голая почва'}
LC_CMAP = {0:(0,0,0,0),1:(33,150,243,255),2:(0,104,55,255),3:(0,200,83,255),
           4:(139,195,74,255),5:(205,220,57,255),6:(215,204,200,255)}

with rasterio.open(LC_PATH) as src:
    cx, cy = src.width // 2, src.height // 2
    S = 2000
    win = Window(cx - S//2, cy - S//2, S, S)
    lc = src.read(1, window=win)

lc_rgb = np.zeros((lc.shape[0], lc.shape[1], 4), dtype=np.uint8)
for k, c in LC_CMAP.items():
    lc_rgb[lc == k] = c

with rasterio.open(NDVI_PATH) as src:
    ndvi_data = src.read(1, window=win)
    if hasattr(ndvi_data, 'mask'):
        ndvi_data = ndvi_data.filled(np.nan)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
ax1.imshow(lc_rgb); ax1.set_title('Классификация (2K×2K px)'); ax1.axis('off')
im = ax2.imshow(ndvi_data, cmap='RdYlGn', vmin=-0.2, vmax=0.8)
ax2.set_title('NDVI оригинал'); ax2.axis('off')
plt.colorbar(im, ax=ax2, fraction=0.046, label='NDVI')
plt.tight_layout(); plt.savefig(OUT / 'landcover_map.png'); plt.close()
print(f"3/4 landcover_map.png")

# ====================== 4. PostGIS данные ======================
print("\n4/4 PostGIS data:\n")
try:
    import psycopg2
    conn = psycopg2.connect(host='localhost', port=5432, dbname='geoai_tko',
                            user='geoai', password='geoai_secret')
    cur = conn.cursor()
    print('=== Zonal Stats ===')
    cur.execute('SELECT region, area_km2, ndvi_mean FROM zonal_stats ORDER BY id DESC LIMIT 1')
    for r in cur.fetchall():
        print(f'  {r[0]}: area={r[1]:,.0f} km², NDVI mean={r[2]:.4f}')

    print('\n=== Landcover ===')
    cur.execute('SELECT class_name, pct FROM landcover_stats ORDER BY pct DESC')
    for r in cur.fetchall():
        print(f'  {r[0]:.<35} {r[1]:.1f}%')

    print('\n=== Derived Layers ===')
    cur.execute('SELECT layer_name, value_mean, crs FROM derived_layers')
    for r in cur.fetchall():
        print(f'  {r[0]}: mean={r[1]:.3f}, crs={r[2]}')
    conn.close()
except Exception as e:
    print(f'  PostGIS error: {e}')

print(f"\nDone! PNGs: {OUT}")
