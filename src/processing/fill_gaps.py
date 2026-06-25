# Дополнить мозаик — перескачать только пустые тайлы
# Запускать из папки processing/
# Берёт 8 тайлов с неполными данными, MAX_CLOUD=40, апрель-октябрь

import logging
import sys
from pathlib import Path

# ── Импортируем всё из основного скрипта ────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.merge import merge as rio_merge
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from rasterio.transform import from_bounds

try:
    from pystac_client import Client
except ImportError:
    sys.exit("pip install pystac-client")

import requests
import time
from datetime import datetime, timedelta
from shapely.geometry import mapping, box

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fill_gaps")

# ── Конфиг ───────────────────────────────────────────────────────────────────
STAC_URL    = "https://earth-search.aws.element84.com/v1"
COLLECTION  = "sentinel-2-l2a"

# Пустые/неполные тайлы — именно для них расширяем поиск
TARGET_TILES = [
    "41TQF", "41TQM",
    "42TTL",
    "42TUS", "42TVS", "42TWS",
    "42TXL", "42TXS",
]

# Расширенные параметры — больше облаков и шире диапазон дат
DATE_START         = "2023-04-01"   # был июнь, теперь апрель
DATE_END           = "2023-10-31"   # был сентябрь, теперь октябрь
MAX_CLOUD_PERCENT  = 40             # был 20, теперь 40

BANDS = {
    "B02": "blue",
    "B03": "green",
    "B04": "red",
    "B05": "rededge1",
    "B08": "nir",
    "B8A": "nir08",
    "B11": "swir16",
}
SCL_KEY = "scl"

OUTPUT_CRS   = "EPSG:32641"
RESOLUTION   = 10

S2_WORK_DIR  = Path(r"C:\Users\USER\alpha-turkestan\src\processing\s2_work")
DOWNLOAD_DIR = Path(r"D:\data\s2_scenes_fill")
TEMP_DIR     = Path(r"D:\data\s2_temp_fill")
COG_OUTPUT   = Path(r"D:\data\s2_mosaic_cog.tif")

# Маппинг тайл → bbox в EPSG:4326 (приблизительный)
# Sentinel-2 MGRS тайлы 100x100 км
TILE_BBOXES = {
    "41TQF": [65.5, 40.8, 67.5, 41.9],
    "41TQM": [65.5, 41.8, 67.5, 42.9],
    "42TTL": [66.5, 40.8, 68.5, 41.9],
    "42TUS": [68.5, 40.8, 70.5, 41.9],
    "42TVS": [70.5, 40.8, 72.5, 41.9],
    "42TWS": [72.5, 40.8, 74.5, 41.9],
    "42TXL": [74.5, 40.8, 76.5, 41.9],
    "42TXS": [74.5, 40.8, 76.5, 41.9],
}


# ── Вспомогательные функции ──────────────────────────────────────────────────

BAND_ALIAS = {
    "B02": ["blue",      "B02", "b02"],
    "B03": ["green",     "B03", "b03"],
    "B04": ["red",       "B04", "b04"],
    "B05": ["rededge1",  "B05", "b05"],
    "B08": ["nir",       "B08", "b08"],
    "B8A": ["nir08",     "B8A", "b8a"],
    "B11": ["swir16",    "B11", "b11"],
    "SCL": ["scl",       "SCL"],
}


def get_href(item, band):
    assets = item.assets if hasattr(item, "assets") else item.get("assets", {})
    for key in BAND_ALIAS.get(band.upper(), [band]):
        if key in assets:
            asset = assets[key]
            href = asset.href if hasattr(asset, "href") else asset.get("href")
            if href:
                return href
    return None


def download_file(url, dest, retries=3):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return True
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            return True
        except Exception as exc:
            wait = 2 ** attempt
            log.warning("  Attempt %d failed: %s. Retry in %ds", attempt, exc, wait)
            time.sleep(wait)
    return False


def reproject_band(src_path, dst_path):
    try:
        with rasterio.open(src_path) as src:
            transform, width, height = calculate_default_transform(
                src.crs, CRS.from_string(OUTPUT_CRS),
                src.width, src.height, *src.bounds
            )
            transform = rasterio.transform.from_origin(
                transform.c, transform.f, RESOLUTION, RESOLUTION
            )
            width  = max(1, int((src.bounds.right - src.bounds.left)  / RESOLUTION))
            height = max(1, int((src.bounds.top   - src.bounds.bottom) / RESOLUTION))

            profile = src.profile.copy()
            profile.update(
                crs=OUTPUT_CRS, transform=transform,
                width=width, height=height,
                driver="GTiff", compress="lzw",
                tiled=True, blockxsize=512, blockysize=512,
            )
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(dst_path, "w", **profile) as dst:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=rasterio.band(dst, 1),
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=transform, dst_crs=CRS.from_string(OUTPUT_CRS),
                    resampling=Resampling.bilinear,
                )
        return True
    except Exception as e:
        log.warning("  Reproject failed: %s", e)
        return False


def apply_scl_mask(data_path, scl_path):
    """Маскируем облачные пиксели через SCL."""
    CLOUD_CLASSES = {3, 8, 9, 10}
    try:
        with rasterio.open(scl_path) as scl_src:
            scl = scl_src.read(1)
        cloud_mask = np.isin(scl, list(CLOUD_CLASSES))
        with rasterio.open(data_path, "r+") as src:
            for b in range(1, src.count + 1):
                data = src.read(b)
                data[cloud_mask] = 0
                src.write(data, b)
        return True
    except Exception as e:
        log.warning("  SCL mask failed: %s", e)
        return False


# ── Поиск сцен для одного тайла ──────────────────────────────────────────────

def search_tile_scenes(tile_name):
    bbox = TILE_BBOXES.get(tile_name)
    if not bbox:
        log.warning("Bbox not found for tile %s", tile_name)
        return []

    catalog = Client.open(STAC_URL)
    geometry = mapping(box(*bbox))

    items = {}
    fmt = "%Y-%m-%d"
    cur = datetime.strptime(DATE_START, fmt)
    stop = datetime.strptime(DATE_END, fmt)

    while cur < stop:
        nxt = min(cur + timedelta(days=30), stop)
        win = f"{cur.strftime(fmt)}/{nxt.strftime(fmt)}"
        cur = nxt

        search = catalog.search(
            collections=[COLLECTION],
            intersects=geometry,
            datetime=win,
            max_items=None,
            limit=100,
        )
        for page in search.pages():
            for item in page:
                props = item.properties if hasattr(item, "properties") else item.get("properties", {})
                cloud = float(props.get("eo:cloud_cover", 999) or 999)
                # Фильтруем по тайлу через scene id
                item_id = item.id if hasattr(item, "id") else item["id"]
                if tile_name in item_id and cloud <= MAX_CLOUD_PERCENT:
                    items[item_id] = item

    result = sorted(items.values(),
                    key=lambda i: float(
                        (i.properties if hasattr(i, "properties") else i.get("properties", {}))
                        .get("eo:cloud_cover", 999) or 999
                    ))
    log.info("  Tile %s: found %d scenes (cloud ≤%d%%)",
             tile_name, len(result), MAX_CLOUD_PERCENT)
    return result


# ── Сборка одного тайла из нескольких сцен ───────────────────────────────────

def build_tile(tile_name, scenes):
    if not scenes:
        log.warning("No scenes for tile %s — skipping", tile_name)
        return False

    band_files = {b: [] for b in BANDS}

    for scene in scenes[:10]:  # берём до 10 лучших сцен
        scene_id = scene.id if hasattr(scene, "id") else scene["id"]
        log.info("  Processing scene %s", scene_id)

        # Скачиваем SCL для маски
        scl_href = get_href(scene, "SCL")
        scl_raw  = DOWNLOAD_DIR / tile_name / scene_id / "SCL.tif"
        scl_warp = TEMP_DIR    / tile_name / scene_id / "SCL_warped.tif"

        if scl_href:
            download_file(scl_href, scl_raw)
            reproject_band(scl_raw, scl_warp)

        ok = True
        scene_bands = {}
        for band in BANDS:
            href = get_href(scene, band)
            if not href:
                ok = False
                break

            raw_path  = DOWNLOAD_DIR / tile_name / scene_id / f"{band}.tif"
            warp_path = TEMP_DIR    / tile_name / scene_id / f"{band}_warped.tif"

            if not (warp_path.exists() and warp_path.stat().st_size > 0):
                if not download_file(href, raw_path):
                    ok = False
                    break
                if not reproject_band(raw_path, warp_path):
                    ok = False
                    break

            scene_bands[band] = warp_path

        if not ok:
            continue

        # Применяем SCL маску
        if scl_warp.exists():
            for band, path in scene_bands.items():
                apply_scl_mask(path, scl_warp)

        for band, path in scene_bands.items():
            band_files[band].append(path)

    # Мержим по каждой банде
    output_path = S2_WORK_DIR / f"tile_{tile_name}.tif"
    band_arrays = []

    with rasterio.open(list(band_files.values())[0][0]) as ref:
        profile = ref.profile.copy()
        height, width = ref.height, ref.width
        transform = ref.transform

    for band in BANDS:
        files = band_files[band]
        if not files:
            log.warning("  No files for band %s in tile %s", band, tile_name)
            band_arrays.append(np.zeros((height, width), dtype=np.uint16))
            continue

        src_files = []
        try:
            for f in files:
                try:
                    # Verify file is readable before adding to merge list
                    with rasterio.open(f) as test:
                        test.read(1, window=rasterio.windows.Window(0, 0, 1, 1))
                    src_files.append(rasterio.open(f))
                except Exception as e:
                    log.warning("  Skipping corrupt file %s: %s", f.name, e)
                    # Delete corrupt file so it gets re-downloaded next run
                    try:
                        f.unlink()
                        log.info("  Deleted corrupt file: %s", f.name)
                    except Exception:
                        pass

            if src_files:
                bounds_utm = transform_bounds(
                    "EPSG:4326", OUTPUT_CRS,
                    *TILE_BBOXES.get(tile_name, [65.9, 40.9, 70.7, 46.2])
                )
                try:
                    mosaic, _ = rio_merge(
                        src_files,
                        bounds=bounds_utm,
                        res=RESOLUTION,
                        method="first",
                        nodata=0,
                    )
                    band_arrays.append(mosaic[0])
                except Exception as e:
                    log.warning("  Merge failed for band %s: %s — using zeros", band, e)
                    band_arrays.append(np.zeros((height, width), dtype=np.uint16))
            else:
                band_arrays.append(np.zeros((height, width), dtype=np.uint16))
        finally:
            for f in src_files:
                try:
                    f.close()
                except Exception:
                    pass

    # Записываем тайл
    profile.update(
        count=len(BANDS),
        compress="lzw",
        tiled=True,
        blockxsize=512,
        blockysize=512,
        nodata=0,
    )

    S2_WORK_DIR.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_path, "w", **profile) as dst:
        for i, arr in enumerate(band_arrays, 1):
            dst.write(arr.astype(np.uint16), i)
        dst.update_tags(TILE=tile_name, BANDS=",".join(BANDS.keys()))

    size_mb = output_path.stat().st_size / 1e6
    log.info("  ✓ Tile %s saved: %s (%.0f MB)", tile_name, output_path.name, size_mb)
    return True


# ── Пересборка COG мозаика ───────────────────────────────────────────────────

def rebuild_cog():
    log.info("Rebuilding COG mosaic from all 37 tiles...")
    tiles = sorted(S2_WORK_DIR.glob("tile_*.tif"))
    log.info("Found %d tiles", len(tiles))

    BLOCK_SIZE = 512
    with rasterio.open(tiles[0]) as first:
        count  = first.count
        dtype  = first.dtypes[0]
        crs    = first.crs
        nodata = first.nodata or 0
        res    = abs(first.transform.a)

    all_bounds = []
    for t in tiles:
        with rasterio.open(t) as src:
            all_bounds.append(src.bounds)

    left   = min(b.left   for b in all_bounds)
    bottom = min(b.bottom for b in all_bounds)
    right  = max(b.right  for b in all_bounds)
    top    = max(b.top    for b in all_bounds)

    width  = int((right - left)   / res)
    height = int((top   - bottom) / res)
    transform = from_bounds(left, bottom, right, top, width, height)

    log.info("Mosaic size: %d × %d px", width, height)

    profile = {
        "driver": "GTiff", "dtype": dtype,
        "width": width, "height": height, "count": count,
        "crs": crs, "transform": transform, "nodata": nodata,
        "compress": "deflate", "tiled": True,
        "blockxsize": BLOCK_SIZE, "blockysize": BLOCK_SIZE,
        "bigtiff": "YES",
    }

    src_files = [rasterio.open(t) for t in tiles]
    n_blocks_y = (height + BLOCK_SIZE - 1) // BLOCK_SIZE
    n_blocks_x = (width  + BLOCK_SIZE - 1) // BLOCK_SIZE
    total = n_blocks_y * n_blocks_x

    with rasterio.open(COG_OUTPUT, "w", **profile) as dst:
        done = 0
        for row_off in range(0, height, BLOCK_SIZE):
            row_h = min(BLOCK_SIZE, height - row_off)
            for col_off in range(0, width, BLOCK_SIZE):
                col_w = min(BLOCK_SIZE, width - col_off)

                block_left   = left + col_off * res
                block_right  = left + (col_off + col_w) * res
                block_top    = top  - row_off * res
                block_bottom = top  - (row_off + row_h) * res

                block_data = np.zeros((count, row_h, col_w), dtype=np.dtype(dtype))

                for src in src_files:
                    if (src.bounds.right <= block_left or
                        src.bounds.left >= block_right or
                        src.bounds.top <= block_bottom or
                        src.bounds.bottom >= block_top):
                        continue

                    win = rasterio.windows.from_bounds(
                        max(block_left, src.bounds.left),
                        max(block_bottom, src.bounds.bottom),
                        min(block_right, src.bounds.right),
                        min(block_top, src.bounds.top),
                        src.transform,
                    )
                    dst_col = int((max(block_left, src.bounds.left) - block_left) / res)
                    dst_row = int((block_top - min(block_top, src.bounds.top)) / res)
                    rh, rw = int(win.height), int(win.width)
                    if rh <= 0 or rw <= 0:
                        continue
                    try:
                        data = src.read(
                            window=win,
                            out_shape=(count, rh, rw),
                            resampling=Resampling.nearest,
                        )
                        valid = data[0] != nodata
                        for b in range(count):
                            sl_dst = block_data[b,
                                dst_row:dst_row+rh, dst_col:dst_col+rw]
                            sl_src = data[b, :rh, :rw]
                            m = valid[:rh, :rw]
                            sl_dst[m] = sl_src[m]
                    except Exception:
                        pass

                dst.write(block_data,
                          window=rasterio.windows.Window(col_off, row_off, col_w, row_h))
                done += 1
                if done % 50 == 0 or done == total:
                    print(f"  [{done/total*100:5.1f}%] block {done}/{total}", end="\r")

    for s in src_files:
        s.close()

    log.info("Building overviews...")
    with rasterio.open(COG_OUTPUT, "r+") as dst:
        dst.build_overviews([2, 4, 8, 16, 32, 64], Resampling.bilinear)
        dst.update_tags(ns="rio_overview", resampling="bilinear")

    size_gb = COG_OUTPUT.stat().st_size / 1e9
    log.info("✓ COG rebuilt: %s (%.1f GB)", COG_OUTPUT, size_gb)


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 55)
    log.info("GeoAI-TKO — Fill gaps in mosaic")
    log.info("Tiles to fill: %s", TARGET_TILES)
    log.info("Date range: %s → %s", DATE_START, DATE_END)
    log.info("Max cloud: %d%%", MAX_CLOUD_PERCENT)
    log.info("=" * 55)

    filled = 0
    for tile_name in TARGET_TILES:
        log.info("\n[Tile %s]", tile_name)
        scenes = search_tile_scenes(tile_name)
        if build_tile(tile_name, scenes):
            filled += 1

    log.info("\n%d/%d tiles filled", filled, len(TARGET_TILES))

    if filled > 0:
        log.info("\nRebuilding COG mosaic...")
        rebuild_cog()
        log.info("\n✓ Done! Restart uvicorn to serve updated mosaic.")
    else:
        log.info("No tiles were updated.")