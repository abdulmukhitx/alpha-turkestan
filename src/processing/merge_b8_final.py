"""Сборка B8 из тайлов — ручное позиционирование."""
import rasterio, numpy as np, os, glob, json
from pathlib import Path
from PIL import Image, ImageDraw
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom
from shapely.geometry import shape, MultiPolygon
from pyproj import Transformer

BASE = Path(r'C:\Users\oqa1a\GeoAI-TKO')
SRC = os.path.expanduser("~/OneDrive/Desktop")
B11_PATH = SRC + "/tko_b11.tif"
TILES = sorted(glob.glob(SRC + "/tko_bbox-*.tif"))
BOUNDARY = BASE / "data/raw/turkestan_boundary.geojson"
OUT = BASE / "data" / "processed"
WEB = BASE / "data" / "web"

print(f"Tiles: {len(TILES)}")

with rasterio.open(B11_PATH) as b11_src:
    b11_arr = b11_src.read(1).astype(np.float32)
    b11_tf = b11_src.transform
    b11_h, b11_w = b11_arr.shape
    profile = b11_src.profile.copy()
    crs = b11_src.crs

# B8 merge
b8 = np.full((b11_h, b11_w), np.nan, dtype=np.float32)

for tile_path in TILES:
    with rasterio.open(tile_path) as tile:
        b8_tile = tile.read(4).astype(np.float32)  # band 4 = B8 (NIR)
        tile_tf = tile.transform
        # Top-left in UTM
        ulx = tile_tf[2]
        uly = tile_tf[5]
        # B11 grid cell size = 20m
        # col_start = (ulx - b11_tf[2]) / 20
        # row_start = (b11_tf[5] - uly) / 20
        # But tile pixels are 10m, B11 grid is 20m → every 2×2 tile pixel = 1 B11 pixel
        # Tile covers 16384×16384 at 10m = 8192×8192 at 20m
        
        col_start = int(round((ulx - b11_tf[2]) / 20))
        row_start = int(round((b11_tf[5] - uly) / 20))
        
        # Average 2×2 blocks to 20m
        th, tw = b8_tile.shape  # 16384×16384
        th2, tw2 = th//2, tw//2  # 8192×8192
        
        # Fast block averaging via reshape
        b8_20 = b8_tile[:th2*2, :tw2*2]  # strip to even
        b8_20 = b8_20.reshape(th2, 2, tw2, 2).mean(axis=(1, 3))
        
        r_end = min(row_start + th2, b11_h)
        c_end = min(col_start + tw2, b11_w)
        r_len = r_end - row_start
        c_len = c_end - col_start
        
        if r_len > 0 and c_len > 0 and row_start >= 0 and col_start >= 0:
            b8[row_start:r_end, col_start:c_end] = b8_20[:r_len, :c_len]
            print(f"  {Path(tile_path).name}: [{row_start}:{r_end}, {col_start}:{c_end}]")

print(f"B8 merged valid: {np.sum(~np.isnan(b8)):,}/{b8.size:,} ({np.sum(~np.isnan(b8))/b8.size*100:.1f}%)")

# NDBI
ndbi = np.full_like(b11_arr, np.nan)
den = b11_arr + b8
valid = (den > 2) & ~np.isnan(b11_arr) & ~np.isnan(b8)
ndbi[valid] = (b11_arr[valid] - b8[valid]) / den[valid]
n = np.sum(~np.isnan(ndbi))
print(f"NDBI valid: {n:,} min={np.nanmin(ndbi):.4f} max={np.nanmax(ndbi):.4f} mean={np.nanmean(ndbi):.4f}")

profile.update(dtype=np.float32, count=1, compress='lzw', bigtiff='YES', nodata=np.nan)
with rasterio.open(OUT / "ndbi_full.tif", 'w', **profile) as dst:
    dst.write(ndbi.astype(np.float32), 1)

# PNG
with open(BOUNDARY) as f:
    poly = shape(json.load(f)['features'][0]['geometry'])
polys = list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]

S = 1800
ndbi_small = ndbi.reshape(S, ndbi.shape[0]//S, -1).mean(axis=1)  # wrong
# Manual resize
sh, sw = 2600, S
by = ndbi.shape[0] // sh
bx = ndbi.shape[1] // sw
ndbi_s = np.full((sh, sw), np.nan, dtype=np.float32)
for ry in range(sh):
    for rx in range(sw):
        block = ndbi[ry*by:(ry+1)*by, rx*bx:(rx+1)*bx]
        v = np.nanmean(block)
        if not np.isnan(v): ndbi_s[ry, rx] = v
    if ry % 500 == 0: print(f"  PNG row {ry}/{sh}")

# Scale transform for boundary 
tf_s = b11_tf * b11_tf.scale(b11_w/sw, b11_h/sh)

mask = np.zeros((sh, sw), dtype=bool)
for p in polys:
    m = geometry_mask([transform_geom('EPSG:4326', crs, json.loads(json.dumps(p.__geo_interface__)))],
                      out_shape=(sh, sw), transform=tf_s, invert=True)
    mask |= m
ndbi_s[~mask] = np.nan

vmin, vmax = 0.0, 0.35
clipped = np.clip(ndbi_s, vmin, vmax)
norm = ((clipped - vmin) / (vmax - vmin) * 255).astype(np.uint8)
colors = [(0,0,139),(65,105,225),(100,149,237),(255,255,255),(255,182,193),(255,105,180)]
lut = np.zeros((256,3), dtype=np.uint8)
for i in range(256):
    t = i / 255.0 * (len(colors)-1); j = int(t); f = t - j; j = min(j, len(colors)-2)
    lut[i] = [int(colors[j][k] + f*(colors[j+1][k]-colors[j][k])) for k in range(3)]
rgb = lut[norm]
rgba = np.dstack([rgb, np.full((sh, sw), 255, dtype=np.uint8)])
rgba[np.isnan(ndbi_s)] = [0,0,0,0]
img = Image.fromarray(rgba, 'RGBA')

t = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
draw = ImageDraw.Draw(img)
for p in polys:
    coords = []
    for lon, lat in p.exterior.coords:
        x, y = t.transform(lon, lat)
        coords.append((int((x - tf_s[2]) / tf_s[0]), int((tf_s[5] - y) / -tf_s[4])))
    draw.line(coords, fill=(0,255,80,230), width=2)
img.save(str(WEB / "ndbi/ndbi_2023.png"))
print(f"PNG: {WEB/'ndbi/ndbi_2023.png'} ({img.size})")
print("DONE")
