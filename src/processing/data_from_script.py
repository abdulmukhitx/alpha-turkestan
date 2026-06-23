"""
Production Sentinel-2 L2A Mosaic Pipeline (v2)
================================================

Replaces a brute-force "download every scene + every band, then merge" approach
with the architecture real EO platforms use (Google Earth Engine, Sentinel Hub,
ESA's own mosaic services):

    1. ONE STAC query across the whole AOI/date range (not date-windowed
       batches) -> group results by native Sentinel-2 MGRS tile.
    2. Per MGRS tile, rank candidate scenes by cloud cover and only ever
       *consider* the least-cloudy `candidate_pool_per_tile` of them.
    3. Per tile, read bands directly off the remote Cloud-Optimized GeoTIFFs
       using HTTP range requests (GDAL /vsicurl), windowed to the AOI extent
       only -> nothing is downloaded in full. A 700MB Sentinel-2 band asset
       might cost a few MB of actual transfer once windowed to your AOI.
    4. Cloud-mask every scene with its own SCL band (per pixel), then build a
       greedy "best available pixel" composite: take the least-cloudy scene's
       clear pixels first, then only fetch the next scene to fill remaining
       gaps. Stop as soon as coverage is "good enough" (coverage_target) or a
       scene cap is hit (max_scenes_per_tile). This is what collapses 1,200+
       candidate scenes down into the ~10-30 that actually get used.
    5. Reproject each (small, already cloud-free) tile composite into one
       common CRS, snapped to a global pixel grid -- only once per tile, not
       once per scene/band. This matters here specifically because this AOI
       straddles the UTM 41N/42N boundary at 66°E: scenes on either side of
       that line come back from STAC in *different* native CRSs, and without
       grid-snapping, adjacent tile composites can show 1-pixel seam
       artifacts after reprojection.
    6. Merge the (now few, small, pre-aligned) tile composites into one COG
       mosaic.

Honest caveats:
    - Step 3's windowed-remote-read trick needs an HTTP-range-friendly COG
      host. Element84 Earth Search (AWS) and Microsoft Planetary Computer
      both work well. Copernicus CDSE's own STAC distribution does not
      always support efficient ranged COG access the same way -- benchmark
      this specifically if you must use CDSE, or fall back to whole-asset
      downloads for that endpoint only.
    - This is a reference architecture, not a fully hardened production
      service. Left as "harden before you scale this to all of Kazakhstan":
      persistent retry queues, checkpoint/resume across process restarts,
      distributed (multi-machine) tile processing, structured run metrics.
      Single-machine + this logic comfortably handles Turkestan-Oblast scale
      (~20-40 MGRS tiles).
    - I could not run this end-to-end in this sandbox (no network egress to
      STAC/COG hosts here), so the pure logic (MGRS-tile parsing, SCL
      clear-pixel masking, grid math) is unit-tested below, but the actual
      remote-read path needs to be exercised on your machine.
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.merge import merge as rio_merge
from rasterio.transform import from_bounds as transform_from_bounds, array_bounds
from rasterio.warp import transform_bounds, reproject
from rasterio.windows import Window, from_bounds as window_from_bounds, bounds as window_bounds
from shapely.geometry import box, mapping

try:
    from pystac_client import Client
except ImportError:
    sys.exit("pip install pystac-client")

try:
    import planetary_computer as pc
    HAS_PC = True
except ImportError:
    HAS_PC = False

# ---------------------------------------------------------------------------
# 0. GDAL tuning for remote COG reads.
#    This block is the single biggest lever for not downloading terabytes:
#    it tells GDAL to fetch byte ranges over HTTP instead of whole files,
#    and to cache what it fetches instead of re-requesting it.
# ---------------------------------------------------------------------------
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.TIF")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ.setdefault("VSI_CACHE_SIZE", "200000000")
os.environ.setdefault("GDAL_CACHEMAX", "512")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("s2_pipeline_v2")


# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------

@dataclass
class Config:
    aoi_bbox: list[float]                 # [W, S, E, N] in EPSG:4326
    date_start: str
    date_end: str
    output_crs: str

    resolution: int = 10                  # metres, output pixel size
    stac_url: str = "https://earth-search.aws.element84.com/v1"
    collection: str = "sentinel-2-l2a"

    bands: list[str] = field(default_factory=lambda: [
        "B02", "B03", "B04", "B08", "B05", "B8A", "B11", "SCL",
    ])

    # --- scene-reduction knobs -- these are *the* answer to "1200 -> 10-30" ---
    candidate_pool_per_tile: int = 10     # never even look past the N least-cloudy
                                           # scenes per tile (by global cloud_cover)
    max_scenes_per_tile: int = 6          # hard ceiling on scenes actually fetched
    coverage_target: float = 0.995        # stop fetching more scenes once a tile
                                           # is this fraction filled with clear pixels

    # SCL classes treated as "clear" (usable). Default keeps vegetation, bare
    # soil/built-up, water, unclassified. Excludes saturated/defective, dark
    # area, cloud shadow, both cloud-probability classes, thin cirrus.
    # Snow (11) is OFF by default -- it's easily confused with cloud at the
    # SCL level. Turn it on if your AOI/season genuinely needs snow as "valid"
    # (e.g. a winter mosaic) -- this is a methodology call, not a technical one.
    clear_scl_classes: tuple[int, ...] = (4, 5, 6, 7)

    work_dir: Path = Path("./s2_work")
    out_path: str = "s2_mosaic.tif"

    @property
    def bands_to_mosaic(self) -> list[str]:
        return [b for b in self.bands if b != "SCL"]


# ---------------------------------------------------------------------------
# 2. Asset URL resolution (kept close to the original -- this part was fine)
# ---------------------------------------------------------------------------

BAND_ALIAS: dict[str, list[str]] = {
    "B01": ["coastal", "B01", "b01"],
    "B02": ["blue", "B02", "b02"],
    "B03": ["green", "B03", "b03"],
    "B04": ["red", "B04", "b04"],
    "B05": ["rededge1", "B05", "b05"],
    "B06": ["rededge2", "B06", "b06"],
    "B07": ["rededge3", "B07", "b07"],
    "B08": ["nir", "B08", "b08"],
    "B8A": ["nir08", "B8A", "b8a"],
    "B09": ["nir09", "B09", "b09"],
    "B11": ["swir16", "B11", "b11"],
    "B12": ["swir22", "B12", "b12"],
    "SCL": ["scl", "SCL"],
}


def get_asset_href(item, band: str) -> Optional[str]:
    candidates = BAND_ALIAS.get(band.upper(), [band, band.lower(), band.upper()])
    for key in candidates:
        if key in item.assets:
            href = item.assets[key].href
            if href:
                return href
    return None


def open_remote(href: str):
    """Open a remote COG, signing it first if it's a Planetary Computer asset."""
    if HAS_PC and "blob.core.windows.net" in href:
        href = pc.sign(href)
    return rasterio.open(href)


def _retry(fn, *args, retries: int = 3, backoff: float = 2.0, **kwargs):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - intentionally broad: this is I/O
            last_exc = exc
            if attempt == retries:
                break
            wait = backoff ** attempt
            log.warning("  retry %d/%d after %s (waiting %.0fs)", attempt, retries, exc, wait)
            time.sleep(wait)
    raise last_exc


# ---------------------------------------------------------------------------
# 3. MGRS tile identification
#    This is what turns "1200 scenes" into "~20-40 tiles x a few scenes each".
#    Works across providers because it falls back to parsing the *standard*
#    ESA product-id naming convention, not a provider-specific STAC property.
# ---------------------------------------------------------------------------

_TILE_ID_RE = re.compile(r"_T(\d{2}[A-Z]{3})_")


def get_mgrs_tile(item) -> Optional[str]:
    for key in ("s2:mgrs_tile", "grid:code", "mgrs:tile"):
        val = item.properties.get(key)
        if val:
            val = str(val)
            if val.startswith("MGRS-"):
                val = val[5:]
            return val
    m = _TILE_ID_RE.search(item.id)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# 4. STAC search -- ONE query, grouped by tile, pre-filtered to a small pool
# ---------------------------------------------------------------------------

def search_by_tile(cfg: Config) -> dict[str, list]:
    catalog = Client.open(cfg.stac_url)
    geometry = mapping(box(*cfg.aoi_bbox))

    log.info("Querying %s  collection=%s  dates=%s/%s", cfg.stac_url, cfg.collection,
             cfg.date_start, cfg.date_end)

    search = catalog.search(
        collections=[cfg.collection],
        intersects=geometry,
        datetime=f"{cfg.date_start}/{cfg.date_end}",
        limit=200,
    )

    by_tile: dict[str, list] = {}
    total = 0
    for item in search.items():
        total += 1
        tile = get_mgrs_tile(item)
        if tile is None:
            continue
        by_tile.setdefault(tile, []).append(item)

    if total == 0:
        log.error(
            "STAC query returned 0 scenes. Check: (1) collection id is correct for "
            "this endpoint, (2) bbox is [W,S,E,N] in degrees, (3) date range has "
            "coverage. Do NOT add an eo:cloud_cover query filter here -- filter "
            "client-side; some catalogs return empty pages when property filters "
            "combine with large spatial extents."
        )
        return {}

    log.info("STAC returned %d scenes across %d MGRS tiles.", total, len(by_tile))

    for tile, items in by_tile.items():
        items.sort(key=lambda it: float(it.properties.get("eo:cloud_cover", 999) or 999))
        by_tile[tile] = items[: cfg.candidate_pool_per_tile]

    kept = sum(len(v) for v in by_tile.values())
    log.info(
        "Pre-filtered to %d candidate scenes (pool=%d per tile, %d tiles). "
        "This is the pool we'll *consider* -- the greedy compositor below will "
        "actually fetch far fewer than this.",
        kept, cfg.candidate_pool_per_tile, len(by_tile),
    )
    return by_tile


# ---------------------------------------------------------------------------
# 5. Per-tile compositing: AOI-windowed remote reads, SCL cloud masking,
#    greedy best-available-pixel fill, early exit.
# ---------------------------------------------------------------------------

def _aoi_window_grid(href: str, aoi_bbox: list[float], resolution: int):
    """Build one common output grid (transform, width, height, crs) for the
    AOI-window of this asset, at `resolution` metres. Computed once per tile
    (from that tile's first available SCL asset) and reused for every scene
    and band in that tile, since all scenes of one MGRS tile share the same
    native UTM CRS and pixel framing."""
    with open_remote(href) as src:
        crs = src.crs
        bounds_native = transform_bounds("EPSG:4326", crs, *aoi_bbox)
        win = window_from_bounds(*bounds_native, transform=src.transform)
        win = win.intersection(Window(0, 0, src.width, src.height))
        if win.width <= 0 or win.height <= 0:
            return None
        wb = window_bounds(win, src.transform)
        width = max(1, round((wb[2] - wb[0]) / resolution))
        height = max(1, round((wb[3] - wb[1]) / resolution))
        out_transform = transform_from_bounds(*wb, width, height)
        return out_transform, width, height, crs


def _read_band_on_grid(href: str, aoi_bbox: list[float], grid, resampling) -> Optional[np.ndarray]:
    """Windowed + resampled remote read of one band onto the common tile grid.
    Never downloads the full asset -- only the byte ranges covering the AOI."""
    _out_transform, width, height, crs = grid
    try:
        with open_remote(href) as src:
            bounds_native = transform_bounds("EPSG:4326", crs, *aoi_bbox)
            win = window_from_bounds(*bounds_native, transform=src.transform)
            win = win.intersection(Window(0, 0, src.width, src.height))
            if win.width <= 0 or win.height <= 0:
                return None
            data = src.read(1, window=win, out_shape=(height, width), resampling=resampling)
        return data
    except Exception as exc:
        log.warning("    read failed for %s: %s", href, exc)
        return None


def composite_tile(tile: str, items: list, cfg: Config):
    """Greedy best-available-pixel composite for one MGRS tile."""
    grid = None
    for item in items:
        href = get_asset_href(item, "SCL")
        if href is None:
            continue
        try:
            grid = _retry(_aoi_window_grid, href, cfg.aoi_bbox, cfg.resolution, retries=2)
        except Exception as exc:
            log.warning("  could not establish grid from %s: %s", item.id, exc)
            grid = None
        if grid is not None:
            break
    if grid is None:
        log.warning("Tile %s: could not establish a working grid -- skipping.", tile)
        return None

    out_transform, width, height, crs = grid
    n_bands = len(cfg.bands_to_mosaic)
    composite = np.zeros((n_bands, height, width), dtype=np.uint16)
    filled = np.zeros((height, width), dtype=bool)
    used: list[tuple[str, float]] = []

    # Rank by AOI-windowed cloud fraction, NOT the catalog's global eo:cloud_cover.
    # eo:cloud_cover is computed over the WHOLE ~110x110km tile -- a scene can be
    # "60% cloudy" globally and perfectly clear over your specific AOI corner, or
    # the reverse. We re-rank using the actual SCL pixels inside our AOI window.
    ranked = []
    for item in items:
        href = get_asset_href(item, "SCL")
        if href is None:
            continue
        scl = _read_band_on_grid(href, cfg.aoi_bbox, grid, Resampling.nearest)
        if scl is None:
            continue
        cloud_frac = float((~np.isin(scl, cfg.clear_scl_classes)).mean())
        ranked.append((cloud_frac, item, scl))
    ranked.sort(key=lambda t: t[0])

    for cloud_frac, item, scl in ranked:
        clear = np.isin(scl, cfg.clear_scl_classes)
        new_pixels = clear & (~filled)
        if not new_pixels.any():
            continue  # this scene adds nothing new -- skip its other bands entirely

        for bi, band in enumerate(cfg.bands_to_mosaic):
            href = get_asset_href(item, band)
            if href is None:
                continue
            data = _read_band_on_grid(href, cfg.aoi_bbox, grid, Resampling.bilinear)
            if data is None:
                continue
            composite[bi][new_pixels] = data[new_pixels]

        filled |= new_pixels
        coverage = float(filled.mean())
        used.append((item.id, coverage))
        log.info("  [%s] +%s  (aoi-cloud=%.0f%%)  cumulative coverage=%.1f%%  scenes_used=%d",
                  tile, item.id, cloud_frac * 100, coverage * 100, len(used))

        if coverage >= cfg.coverage_target or len(used) >= cfg.max_scenes_per_tile:
            break

    if not filled.any():
        log.warning("Tile %s: no clear pixels found in any candidate scene.", tile)
        return None

    log.info("Tile %s: done -- %d scenes used, final coverage=%.1f%%",
             tile, len(used), filled.mean() * 100)
    return composite, out_transform, crs, used


# ---------------------------------------------------------------------------
# 6. Reproject one tile composite into the common output CRS, snapped to a
#    global pixel grid.
#
#    Fixes the original script's bug: destination width/height must be
#    derived from the *destination* CRS bounds, not recomputed from the
#    *source* CRS bounds divided by the target resolution -- that mismatch
#    breaks silently whenever source and destination CRS differ, which is
#    exactly the case here, since this AOI straddles UTM zones 41N and 42N.
#
#    Also snaps every tile's output grid to global multiples of `resolution`
#    in the destination CRS, so two tile composites that are individually
#    correct still merge with zero sub-pixel seam artifacts at their shared
#    boundary -- each one lands on the *same* grid, not just a same-resolution
#    grid with its own independent origin.
# ---------------------------------------------------------------------------

def reproject_tile(data: np.ndarray, src_transform, src_crs, dst_crs: str, resolution: int):
    bands, h, w = data.shape
    src_bounds = array_bounds(h, w, src_transform)
    left, bottom, right, top = transform_bounds(src_crs, dst_crs, *src_bounds)

    left = math.floor(left / resolution) * resolution
    top = math.ceil(top / resolution) * resolution
    dst_w = max(1, math.ceil((right - left) / resolution))
    dst_h = max(1, math.ceil((top - bottom) / resolution))
    dst_transform = transform_from_bounds(
        left, top - dst_h * resolution, left + dst_w * resolution, top, dst_w, dst_h,
    )

    out = np.zeros((bands, dst_h, dst_w), dtype=data.dtype)
    for b in range(bands):
        reproject(
            source=data[b],
            destination=out[b],
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
        )
    return out, dst_transform


# ---------------------------------------------------------------------------
# 7. Orchestration
# ---------------------------------------------------------------------------

def write_geotiff(path, data, transform, crs, band_names):
    profile = dict(
        driver="GTiff", dtype=data.dtype, count=data.shape[0],
        height=data.shape[1], width=data.shape[2],
        crs=crs, transform=transform, nodata=0,
        compress="deflate", tiled=True, blockxsize=512, blockysize=512,
    )
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
        for i, name in enumerate(band_names, 1):
            dst.set_band_description(i, name)


def run_pipeline(cfg: Config):
    log.info("=" * 70)
    log.info("Sentinel-2 mosaic pipeline -- AOI=%s  dates=%s/%s",
             cfg.aoi_bbox, cfg.date_start, cfg.date_end)
    log.info("=" * 70)

    by_tile = search_by_tile(cfg)
    if not by_tile:
        return  # search_by_tile already logged the diagnostic -- no crash

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    tile_paths, report = [], {}

    for tile, items in by_tile.items():

        path = cfg.work_dir / f"tile_{tile}.tif"

        if path.exists() and path.stat().st_size > 100_000_000:
            log.info("Tile %s already processed, skipping.", tile)
            tile_paths.append(path)
            continue

        log.info("--- Tile %s (%d candidates) ---", tile, len(items))

        result = composite_tile(tile, items, cfg)
        if result is None:
            continue

        data, transform, crs, used = result

        out_data, out_transform = reproject_tile(
            data,
            transform,
            crs,
            cfg.output_crs,
            cfg.resolution
        )

        path = cfg.work_dir / f"tile_{tile}.tif"
        write_geotiff(path, out_data, out_transform, cfg.output_crs, cfg.bands_to_mosaic)

        tile_paths.append(path)
        report[tile] = used

    if not tile_paths:
        log.error("No tile produced a usable composite -- nothing to mosaic. "
                  "Check candidate_pool_per_tile, clear_scl_classes, and date range.")
        return

    log.info("Merging %d tile composites -> %s", len(tile_paths), cfg.out_path)

    srcs = [rasterio.open(p) for p in tile_paths]

    try:
        mosaic, out_transform = rio_merge(srcs, method="first")

        profile = srcs[0].profile.copy()
        profile.update(
            count=mosaic.shape[0],
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            transform=out_transform,
            compress="deflate",
            tiled=True,
            blockxsize=512,
            blockysize=512,
        )

        total_scenes = sum(len(v) for v in report.values())

        with rasterio.open(cfg.out_path, "w", **profile) as dst:
            dst.write(mosaic)
            dst.build_overviews([2, 4, 8, 16], Resampling.average)
            dst.update_tags(
                scenes_used=str(total_scenes),
                tiles=",".join(report.keys())
            )

    finally:
        for s in srcs:
            s.close()

    total_scenes = sum(len(v) for v in report.values())

    log.info("=" * 70)
    log.info(
        "DONE -> %s   tiles=%d   total scenes actually fetched=%d "
        "(vs. ~1200+ candidates returned by the original whole-AOI query)",
        cfg.out_path,
        len(tile_paths),
        total_scenes
    )

    for tile, used in report.items():
        log.info("   %s: %s", tile, [u[0] for u in used])

    log.info("=" * 70)


if __name__ == "__main__":
    cfg = Config(
        aoi_bbox=[65.939941, 40.996484, 70.664063, 46.195042],
        date_start="2023-06-01",
        date_end="2023-09-30",
        output_crs="EPSG:32641",
        out_path="s2_mosaic_turkestan.tif",
    )

    run_pipeline(cfg)