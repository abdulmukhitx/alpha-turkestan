"""Unattended Sentinel-2 historical mosaic pipeline for GeoAI TKO.

The default run builds the five missing summer periods (2018-2022) from
Copernicus Data Space Ecosystem (CDSE) data.  Every stage is restart-safe:

1. Query public CDSE STAC metadata and select orbit-diverse candidates.
2. Download only the seven required JP2 bands with token refresh, partial-file
   resume, integrity checks, and candidate replacement.
3. Reproject and composite each MGRS tile into physical reflectance using a
   bounded amount of memory.  If the real valid-data footprint is incomplete,
   additional candidates are downloaded and the tile is rebuilt.
4. Merge the tiles to a resumable staging raster and translate it with GDAL's
   COG driver.
5. Read the result back block-by-block, validate coverage inside the official
   Turkestan boundary, reflectance ranges, COG structure, and vegetation
   sanity statistics, then write qa_report.json and metadata.json.

Credentials must use the CDSE password grant (an account without MFA):

    CDSE_USERNAME=account@example.com
    CDSE_PASSWORD=...

Typical Linux invocation (S2_DATA_ROOT should be on a large data volume):

    python src/processing/historical_mosaic_pipeline.py \
      --years 2018 2019 2020 2021 2022 --data-root /srv/geoai-tko-data

The systemd unit in deploy/ubuntu/geoai-s2-history.service restarts this
command after machine, network, or CDSE interruptions.  A restart never
redownloads or rebuilds an artifact that passes its read-back validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import rasterio
import requests
from dotenv import load_dotenv
from PIL import Image
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.shutil import copy as raster_copy
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from rasterio.windows import Window, bounds as window_bounds, transform as window_transform
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shapely_transform, unary_union


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

STAC_SEARCH_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
COLLECTION = "sentinel-2-l2a"
SOURCE_NAME = "cdse_direct"
PIPELINE_VERSION = "historical-mosaic-v1"

BANDS = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]
ASSET_KEYS = {
    "B02": "B02_10m",
    "B03": "B03_10m",
    "B04": "B04_10m",
    "B05": "B05_20m",
    "B08": "B08_10m",
    "B8A": "B8A_20m",
    "B11": "B11_20m",
}
TARGET_CRS = CRS.from_epsg(32641)
TARGET_RESOLUTION = 10.0
FLOAT_NODATA = np.float32(-9999.0)
BLOCK_SIZE = 512
OVERVIEW_FACTORS = [2, 4, 8, 16, 32, 64]
BASELINE_RE = re.compile(r"_N(\d{4})_")
MGRS_RE = re.compile(r"_T(\d{2}[A-Z]{3})_")

# Same padded discovery AOI that produced the verified 43-tile archive.  QA is
# always performed against the exact boundary GeoJSON, not this rectangle.
DEFAULT_AOI = {
    "type": "Polygon",
    "coordinates": [[
        [65.928955, 40.530502],
        [70.971680, 40.530502],
        [70.971680, 46.035109],
        [65.928955, 46.035109],
        [65.928955, 40.530502],
    ]],
}


class PipelineError(RuntimeError):
    """A stage could not produce a trustworthy result."""


class DownloadError(PipelineError):
    """A CDSE asset stayed unavailable after bounded retries."""


class ValidationError(PipelineError):
    """An output failed read-back validation."""


@dataclass(frozen=True)
class YearPaths:
    year: int
    data_root: Path

    @property
    def raw_dir(self) -> Path:
        return self.data_root / "raw" / f"s2_{self.year}"

    @property
    def catalog(self) -> Path:
        return self.raw_dir / "catalog.json"

    @property
    def manifest(self) -> Path:
        return self.raw_dir / "manifest.json"

    @property
    def work_dir(self) -> Path:
        return self.data_root / "work" / f"s2_{self.year}_reprojected"

    @property
    def mosaic_dir(self) -> Path:
        return self.data_root / "mosaics" / f"{self.year}_summer"

    @property
    def staging(self) -> Path:
        return self.mosaic_dir / "s2_mosaic_staging.tif"

    @property
    def staging_meta(self) -> Path:
        return self.mosaic_dir / "staging_metadata.json"

    @property
    def merge_state(self) -> Path:
        return self.mosaic_dir / "merge_state.json"

    @property
    def cog(self) -> Path:
        return self.mosaic_dir / "s2_mosaic_cog.tif"

    @property
    def qa_report(self) -> Path:
        return self.mosaic_dir / "qa_report.json"

    @property
    def metadata(self) -> Path:
        return self.mosaic_dir / "metadata.json"

    @property
    def state(self) -> Path:
        return self.mosaic_dir / "pipeline_state.json"


@dataclass
class PipelineConfig:
    data_root: Path
    boundary_path: Path
    reference_year: int = 2025
    cloud_max: float = 40.0
    fallback_cloud_max: float = 60.0
    top_n: int = 3
    max_candidates: int = 6
    download_attempts: int = 8
    http_attempts: int = 8
    tile_nodata_max_pct: float = 0.5
    aoi_nodata_max_pct: float = 0.5
    gdal_threads: str = "4"
    cog_compression: str = "ZSTD"
    cog_level: int = 1
    strict_cog: bool = True
    cleanup_raw_after_qa: bool = False
    cleanup_work_after_qa: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S%z')}] {message}", flush=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def update_state(paths: YearPaths, stage: str, status: str, **extra: Any) -> None:
    state = read_json(paths.state, {}) or {}
    state.update({
        "year": paths.year,
        "pipeline_version": PIPELINE_VERSION,
        "stage": stage,
        "status": status,
        "updated_at": utc_now(),
        **extra,
    })
    atomic_write_json(paths.state, state)


def retry_delay(attempt: int, cap: int = 300) -> int:
    return min(cap, 2 ** min(attempt, 8))


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    attempts: int,
    retry_statuses: set[int] | None = None,
    **kwargs: Any,
) -> requests.Response:
    retry_statuses = retry_statuses or {408, 425, 429, 500, 502, 503, 504}
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.request(method, url, **kwargs)
            if response.status_code in retry_statuses:
                retry_after = response.headers.get("Retry-After", "")
                response.close()
                try:
                    wait = max(1, min(600, int(retry_after)))
                except ValueError:
                    wait = retry_delay(attempt)
                raise requests.HTTPError(f"transient HTTP status; retry in {wait}s")
            if response.status_code >= 400:
                status = response.status_code
                detail = response.text[:300]
                response.close()
                raise PipelineError(f"non-retryable HTTP {status} from {url}: {detail}")
            return response
        except PipelineError:
            raise
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            wait = retry_delay(attempt)
            log(f"HTTP retry {attempt}/{attempts} for {url}: {type(exc).__name__}; waiting {wait}s")
            time.sleep(wait)
    raise PipelineError(f"HTTP request failed after {attempts} attempts: {url}: {last_error}")


class TokenManager:
    def __init__(self, session: requests.Session, username: str, password: str, attempts: int):
        self.session = session
        self.username = username
        self.password = password
        self.attempts = attempts
        self.token: str | None = None
        self.expires_at = 0.0

    def invalidate(self) -> None:
        self.token = None
        self.expires_at = 0.0

    def get(self) -> str:
        if self.token and time.time() < self.expires_at - 180:
            return self.token
        response = request_with_retry(
            self.session,
            "POST",
            TOKEN_URL,
            attempts=self.attempts,
            data={
                "grant_type": "password",
                "username": self.username,
                "password": self.password,
                "client_id": "cdse-public",
            },
            timeout=(30, 60),
        )
        try:
            body = response.json()
            self.token = str(body["access_token"])
            self.expires_at = time.time() + int(body.get("expires_in", 1800))
        except (ValueError, KeyError, TypeError) as exc:
            raise PipelineError("CDSE authentication returned an invalid token response") from exc
        finally:
            response.close()
        log("CDSE download token refreshed")
        return self.token


def parse_baseline(product_id: str, stac_baseline: Any = None) -> tuple[str | None, float | None]:
    if stac_baseline not in (None, ""):
        text = str(stac_baseline).strip()
        try:
            return text, float(text)
        except ValueError:
            pass
    match = BASELINE_RE.search(product_id)
    if not match:
        return None, None
    digits = match.group(1)
    return f"{digits[:2]}.{digits[2:]}", float(f"{digits[:2]}.{digits[2:]}")


def fallback_reflectance_recipe(product_id: str, stac_baseline: Any = None) -> tuple[float, float, str]:
    """Return scale, additive reflectance offset, and provenance.

    Direct CDSE products at baseline >=04.00 use (DN - 1000) / 10000.
    Earlier baselines use DN / 10000.  This fallback is used only when an
    asset does not publish raster:bands scale/offset metadata.
    """
    _, baseline = parse_baseline(product_id, stac_baseline)
    if baseline is None:
        raise PipelineError(f"cannot determine processing baseline for {product_id}")
    return 0.0001, (-0.1 if baseline >= 4.0 else 0.0), "product_id_processing_baseline"


def mgrs_tile(feature: dict[str, Any]) -> str | None:
    properties = feature.get("properties") or {}
    tile = str(properties.get("grid:code") or "").replace("MGRS-", "").strip()
    if tile:
        return tile
    match = MGRS_RE.search(str(feature.get("id") or ""))
    return match.group(1) if match else None


def asset_https_href(asset: dict[str, Any]) -> str | None:
    alternate = asset.get("alternate") or {}
    https_alt = alternate.get("https") or {}
    return https_alt.get("href") or asset.get("href")


def asset_recipe(asset: dict[str, Any], product_id: str, stac_baseline: Any) -> tuple[float, float, str]:
    raster_bands = asset.get("raster:bands") or []
    first = raster_bands[0] if raster_bands and isinstance(raster_bands[0], dict) else {}
    if first.get("scale") is not None:
        scale = float(first["scale"])
        offset = float(first.get("offset", 0.0))
        return scale, offset, "stac_raster_bands"
    return fallback_reflectance_recipe(product_id, stac_baseline)


def simplify_candidate(feature: dict[str, Any]) -> dict[str, Any] | None:
    product_id = str(feature.get("id") or "")
    tile = mgrs_tile(feature)
    properties = feature.get("properties") or {}
    cloud = properties.get("eo:cloud_cover")
    if not product_id or not tile or cloud is None:
        return None
    baseline_text, _ = parse_baseline(product_id, properties.get("processing:baseline"))
    assets = feature.get("assets") or {}
    band_assets: dict[str, Any] = {}
    for band in BANDS:
        asset_key = ASSET_KEYS[band]
        asset = assets.get(asset_key)
        href = asset_https_href(asset or {})
        if not asset or not href:
            return None
        scale, offset, recipe_source = asset_recipe(asset, product_id, properties.get("processing:baseline"))
        expected_size = asset.get("file:size")
        band_assets[band] = {
            "asset_key": asset_key,
            "href": href,
            "expected_size": int(expected_size) if expected_size is not None else None,
            "scale": scale,
            "offset": offset,
            "boa_add_offset_dn": int(round(offset / scale)) if scale else None,
            "recipe_source": recipe_source,
        }
    return {
        "product_id": product_id,
        "tile": tile,
        "datetime": properties.get("datetime"),
        "cloud_cover": round(float(cloud), 3),
        "relative_orbit": str(properties.get("sat:relative_orbit") or ""),
        "processing_baseline": baseline_text,
        "geometry": feature.get("geometry"),
        "assets": band_assets,
        "source": SOURCE_NAME,
    }


def search_summer(session: requests.Session, year: int, aoi: dict[str, Any], attempts: int) -> list[dict[str, Any]]:
    payload: dict[str, Any] | None = {
        "collections": [COLLECTION],
        "intersects": aoi,
        "datetime": f"{year}-06-01T00:00:00Z/{year}-08-31T23:59:59Z",
        "limit": 200,
    }
    url = STAC_SEARCH_URL
    method = "POST"
    features: list[dict[str, Any]] = []
    page = 0
    while url:
        page += 1
        kwargs: dict[str, Any] = {
            "headers": {"Accept-Encoding": "gzip, deflate"},
            "timeout": (30, 120),
        }
        if method == "POST":
            kwargs["json"] = payload
        response = request_with_retry(session, method, url, attempts=attempts, **kwargs)
        try:
            body = response.json()
        except ValueError as exc:
            raise PipelineError(f"CDSE STAC returned invalid JSON for {year}, page {page}") from exc
        finally:
            response.close()
        features.extend(body.get("features") or [])
        next_link = next((link for link in body.get("links", []) if link.get("rel") == "next"), None)
        if not next_link:
            break
        url = str(next_link["href"])
        method = str(next_link.get("method") or "POST").upper()
        payload = next_link.get("body") or payload
    log(f"STAC {year}: received {len(features)} product features across {page} page(s)")
    return features


def select_orbit_diverse(candidates: Iterable[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Choose lowest-cloud products while preferring unrepresented orbits."""
    remaining = sorted(
        list(candidates),
        key=lambda item: (float(item["cloud_cover"]), str(item.get("datetime") or ""), item["product_id"]),
    )
    if not remaining or n <= 0:
        return []
    selected = [remaining.pop(0)]
    used = {selected[0].get("relative_orbit") or ""}
    while remaining and len(selected) < n:
        new_orbit_index = next(
            (
                index
                for index, item in enumerate(remaining)
                if item.get("relative_orbit") and item.get("relative_orbit") not in used
            ),
            None,
        )
        picked = remaining.pop(new_orbit_index if new_orbit_index is not None else 0)
        selected.append(picked)
        used.add(picked.get("relative_orbit") or "")
    return selected


def candidate_pool_for_tile(
    candidates: list[dict[str, Any]], cloud_max: float, fallback_cloud_max: float
) -> list[dict[str, Any]]:
    preferred = [candidate for candidate in candidates if candidate["cloud_cover"] <= cloud_max]
    fallback = [
        candidate
        for candidate in candidates
        if cloud_max < candidate["cloud_cover"] <= fallback_cloud_max
    ]
    worse = [candidate for candidate in candidates if candidate["cloud_cover"] > fallback_cloud_max]
    return sorted(preferred, key=lambda item: item["cloud_cover"]) + sorted(
        fallback, key=lambda item: item["cloud_cover"]
    ) + sorted(worse, key=lambda item: item["cloud_cover"])


def prepare_catalog(
    paths: YearPaths,
    config: PipelineConfig,
    session: requests.Session,
    reference_tiles: list[str],
) -> dict[str, Any]:
    existing = read_json(paths.catalog)
    if (
        existing
        and existing.get("pipeline_version") == PIPELINE_VERSION
        and existing.get("master_tiles") == reference_tiles
        and float(existing.get("cloud_cover_max", -1)) == config.cloud_max
        and float(existing.get("fallback_cloud_cover_max", -1)) == config.fallback_cloud_max
        and int(existing.get("top_n", -1)) == config.top_n
    ):
        return existing

    features = search_summer(session, paths.year, DEFAULT_AOI, config.http_attempts)
    grouped: dict[str, list[dict[str, Any]]] = {tile: [] for tile in reference_tiles}
    for feature in features:
        candidate = simplify_candidate(feature)
        if candidate and candidate["tile"] in grouped:
            grouped[candidate["tile"]].append(candidate)

    missing: list[str] = []
    tiles: dict[str, Any] = {}
    for tile in reference_tiles:
        pool = candidate_pool_for_tile(grouped[tile], config.cloud_max, config.fallback_cloud_max)
        if not pool:
            missing.append(tile)
            continue
        selected = select_orbit_diverse(pool, min(config.top_n, len(pool)))
        tiles[tile] = {
            "candidate_count": len(pool),
            "selected_product_ids": [candidate["product_id"] for candidate in selected],
            "n_unique_orbits_in_top3": len(
                {candidate["relative_orbit"] for candidate in selected if candidate["relative_orbit"]}
            ),
            "pool": pool,
        }
    if missing:
        raise PipelineError(f"{paths.year}: no usable CDSE product for required MGRS tiles {missing}")

    catalog = {
        "pipeline_version": PIPELINE_VERSION,
        "year": paths.year,
        "generated_at": utc_now(),
        "source": SOURCE_NAME,
        "collection": COLLECTION,
        "season": {"start": f"{paths.year}-06-01", "end": f"{paths.year}-08-31"},
        "aoi": DEFAULT_AOI,
        "master_tiles": reference_tiles,
        "cloud_cover_max": config.cloud_max,
        "fallback_cloud_cover_max": config.fallback_cloud_max,
        "top_n": config.top_n,
        "tiles": tiles,
    }
    atomic_write_json(paths.catalog, catalog)
    log(f"{paths.year}: selected orbit-diverse top-{config.top_n} for {len(tiles)} tiles")
    return catalog


def prepare_reference_tiles(config: PipelineConfig, session: requests.Session) -> list[str]:
    cache = config.data_root / f"master_tiles_{config.reference_year}.json"
    existing = read_json(cache)
    if existing and existing.get("tiles"):
        return sorted(set(existing["tiles"]))
    features = search_summer(session, config.reference_year, DEFAULT_AOI, config.http_attempts)
    tiles = sorted({tile for feature in features if (tile := mgrs_tile(feature))})
    if not tiles:
        raise PipelineError(f"could not discover master MGRS tiles from reference year {config.reference_year}")
    atomic_write_json(cache, {"reference_year": config.reference_year, "generated_at": utc_now(), "tiles": tiles})
    log(f"Reference MGRS grid: {len(tiles)} tiles from {config.reference_year}")
    return tiles


def verify_source_raster(path: Path, expected_size: int | None = None) -> tuple[bool, str | None]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return False, "file is missing or empty"
        if expected_size is not None and path.stat().st_size != expected_size:
            return False, f"size {path.stat().st_size} != expected {expected_size}"
        with rasterio.open(path) as dataset:
            if dataset.count != 1 or dataset.width <= 0 or dataset.height <= 0 or dataset.crs is None:
                return False, "invalid raster dimensions/count/CRS"
            windows = [
                Window(0, 0, min(64, dataset.width), min(64, dataset.height)),
                Window(max(0, dataset.width // 2 - 32), max(0, dataset.height // 2 - 32), min(64, dataset.width), min(64, dataset.height)),
                Window(max(0, dataset.width - 64), max(0, dataset.height - 64), min(64, dataset.width), min(64, dataset.height)),
            ]
            for window in windows:
                dataset.read(1, window=window)
        return True, None
    except Exception as exc:  # raster drivers raise several exception types
        return False, f"{type(exc).__name__}: {exc}"


def download_asset(
    session: requests.Session,
    token_manager: TokenManager,
    href: str,
    destination: Path,
    expected_size: int | None,
    attempts: int,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    if expected_size is not None and part.exists() and part.stat().st_size == expected_size:
        part.replace(destination)
        return expected_size
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            if expected_size is not None and part.exists() and part.stat().st_size > expected_size:
                part.unlink()
            offset = part.stat().st_size if part.exists() else 0
            headers = {"Authorization": f"Bearer {token_manager.get()}"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            response = session.get(href, headers=headers, stream=True, timeout=(30, 300))
            if response.status_code == 401:
                response.close()
                token_manager.invalidate()
                raise requests.HTTPError("CDSE token rejected; refreshing")
            if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                status = response.status_code
                response.close()
                raise requests.HTTPError(f"transient HTTP {status}")
            if response.status_code >= 400:
                status = response.status_code
                detail = response.text[:300]
                response.close()
                raise DownloadError(f"non-retryable HTTP {status}: {detail}")
            append = offset > 0 and response.status_code == 206
            mode = "ab" if append else "wb"
            with part.open(mode) as output:
                for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                    if chunk:
                        output.write(chunk)
            response.close()
            actual_size = part.stat().st_size
            if expected_size is not None and actual_size != expected_size:
                raise DownloadError(f"downloaded {actual_size} bytes, expected {expected_size}")
            part.replace(destination)
            return actual_size
        except (requests.RequestException, OSError, DownloadError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            wait = retry_delay(attempt)
            log(
                f"download retry {attempt}/{attempts} for {destination.name}: "
                f"{type(exc).__name__}; waiting {wait}s"
            )
            time.sleep(wait)
    raise DownloadError(f"failed to download {href} after {attempts} attempts: {last_error}")


def candidate_manifest_entry(candidate: dict[str, Any], band_paths: dict[str, str]) -> dict[str, Any]:
    return {
        "product_id": candidate["product_id"],
        "datetime": candidate.get("datetime"),
        "cloud_cover": candidate["cloud_cover"],
        "relative_orbit": candidate.get("relative_orbit"),
        "processing_baseline": candidate.get("processing_baseline"),
        "source": SOURCE_NAME,
        "bands": band_paths,
        "asset_metadata": {
            band: {
                "asset_key": candidate["assets"][band]["asset_key"],
                "expected_size": candidate["assets"][band]["expected_size"],
                "scale": candidate["assets"][band]["scale"],
                "offset": candidate["assets"][band]["offset"],
                "boa_add_offset_dn": candidate["assets"][band]["boa_add_offset_dn"],
                "recipe_source": candidate["assets"][band]["recipe_source"],
            }
            for band in BANDS
        },
    }


def ensure_candidate_downloaded(
    paths: YearPaths,
    candidate: dict[str, Any],
    session: requests.Session,
    token_manager: TokenManager,
    config: PipelineConfig,
) -> dict[str, Any]:
    product_dir = paths.raw_dir / candidate["tile"] / candidate["product_id"]
    band_paths: dict[str, str] = {}
    for band in BANDS:
        asset = candidate["assets"][band]
        destination = product_dir / f"{band}.jp2"
        valid = False
        reason: str | None = None
        for integrity_attempt in range(1, 3):
            valid, reason = verify_source_raster(destination, asset["expected_size"])
            if valid:
                break
            if destination.exists():
                log(
                    f"{paths.year} {candidate['tile']} {candidate['product_id']} {band}: "
                    f"removing invalid file ({reason})"
                )
                destination.unlink()
            part = destination.with_name(destination.name + ".part")
            # Keep a correctly sized partial file for HTTP Range resume. A
            # larger partial can never be valid and is safely discarded.
            if (
                part.exists()
                and asset["expected_size"] is not None
                and part.stat().st_size > asset["expected_size"]
            ):
                part.unlink()
            download_asset(
                session,
                token_manager,
                asset["href"],
                destination,
                asset["expected_size"],
                config.download_attempts,
            )
            valid, reason = verify_source_raster(destination, asset["expected_size"])
            if valid:
                break
            log(
                f"{paths.year} {candidate['tile']} {candidate['product_id']} {band}: "
                f"integrity retry {integrity_attempt}/2 failed ({reason})"
            )
            destination.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        if not valid:
            raise DownloadError(f"{destination} failed raster integrity validation twice: {reason}")
        band_paths[band] = str(destination.resolve())
    return candidate_manifest_entry(candidate, band_paths)


def manifest_entry_is_valid(entry: dict[str, Any]) -> bool:
    try:
        for band in BANDS:
            path = Path(entry["bands"][band])
            expected = entry["asset_metadata"][band].get("expected_size")
            valid, _ = verify_source_raster(path, expected)
            if not valid:
                return False
        return True
    except (KeyError, TypeError):
        return False


def initialize_manifest(paths: YearPaths, catalog: dict[str, Any]) -> dict[str, Any]:
    manifest = read_json(paths.manifest)
    if manifest and manifest.get("pipeline_version") == PIPELINE_VERSION:
        return manifest
    return {
        "pipeline_version": PIPELINE_VERSION,
        "year": paths.year,
        "generated_at": utc_now(),
        "updated_at": utc_now(),
        "source": SOURCE_NAME,
        "collection": COLLECTION,
        "season": catalog["season"],
        "bands": BANDS,
        "target_crs": str(TARGET_CRS),
        "target_resolution_m": TARGET_RESOLUTION,
        "tiles": {},
    }


def download_initial_candidates(
    paths: YearPaths,
    catalog: dict[str, Any],
    config: PipelineConfig,
    session: requests.Session,
    token_manager: TokenManager,
) -> dict[str, Any]:
    manifest = initialize_manifest(paths, catalog)
    total_tiles = len(catalog["master_tiles"])
    for tile_index, tile in enumerate(catalog["master_tiles"], 1):
        tile_catalog = catalog["tiles"][tile]
        pool = tile_catalog["pool"]
        by_id = {candidate["product_id"]: candidate for candidate in pool}
        existing_entries = {
            entry["product_id"]: entry
            for entry in (manifest.get("tiles", {}).get(tile, {}).get("candidates") or [])
            if manifest_entry_is_valid(entry)
        }
        preferred_ids = tile_catalog["selected_product_ids"] + [
            candidate["product_id"]
            for candidate in select_orbit_diverse(pool, len(pool))
            if candidate["product_id"] not in tile_catalog["selected_product_ids"]
        ]
        required = min(config.top_n, len(pool))
        completed: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        log(f"{paths.year} tile {tile_index}/{total_tiles} {tile}: ensuring {required} source products")
        for product_id in preferred_ids:
            if len(completed) >= required:
                break
            if product_id in existing_entries:
                completed.append(existing_entries[product_id])
                continue
            try:
                completed.append(
                    ensure_candidate_downloaded(paths, by_id[product_id], session, token_manager, config)
                )
                log(f"{paths.year} {tile}: completed {product_id}")
            except DownloadError as exc:
                errors.append({"product_id": product_id, "error": str(exc)})
                log(f"{paths.year} {tile}: candidate failed; trying replacement: {exc}")
        if len(completed) < required:
            raise DownloadError(
                f"{paths.year} {tile}: only {len(completed)}/{required} candidates downloaded; errors={errors}"
            )
        orbits = {entry.get("relative_orbit") for entry in completed if entry.get("relative_orbit")}
        manifest.setdefault("tiles", {})[tile] = {
            "candidates": completed,
            "n_unique_orbits_in_top3": len(orbits),
            "single_orbit_requires_pixel_coverage_check": len(orbits) < 2,
            "download_errors_replaced": errors,
        }
        manifest["updated_at"] = utc_now()
        atomic_write_json(paths.manifest, manifest)
    return manifest


def source_products_fingerprint(candidates: list[dict[str, Any]]) -> str:
    payload = [
        {
            "product_id": candidate["product_id"],
            "bands": candidate["bands"],
            "asset_metadata": candidate["asset_metadata"],
        }
        for candidate in candidates
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def validate_tile(path: Path, source_fingerprint: str | None = None) -> dict[str, Any]:
    try:
        with rasterio.open(path) as dataset:
            is_tiled = bool(dataset.profile.get("tiled", False))
            structural = (
                dataset.count == len(BANDS)
                and set(dataset.dtypes) == {"float32"}
                and dataset.crs == TARGET_CRS
                and abs(dataset.res[0] - TARGET_RESOLUTION) < 1e-6
                and abs(dataset.res[1] - TARGET_RESOLUTION) < 1e-6
                and dataset.nodata == float(FLOAT_NODATA)
                and is_tiled
            )
            if not structural:
                return {"passed": False, "reason": "tile structural metadata mismatch"}
            if source_fingerprint and dataset.tags().get("SOURCE_FINGERPRINT") != source_fingerprint:
                return {"passed": False, "reason": "tile source fingerprint changed"}
            total = 0
            nodata = 0
            out_of_range = 0
            non_finite = 0
            for _, window in dataset.block_windows(1):
                values = dataset.read(window=window)
                valid = values[0] != dataset.nodata
                total += valid.size
                nodata += int((~valid).sum())
                if valid.any():
                    selected = values[:, valid]
                    non_finite += int((~np.isfinite(selected)).sum())
                    out_of_range += int(((selected < 0.0) | (selected > 1.0001)).sum())
            nodata_pct = 100.0 * nodata / total if total else 100.0
            passed = non_finite == 0 and out_of_range == 0
            return {
                "passed": passed,
                "nodata_pct": round(nodata_pct, 4),
                "non_finite_values": non_finite,
                "out_of_range_values": out_of_range,
                "width": dataset.width,
                "height": dataset.height,
                "bounds": list(dataset.bounds),
                "mean_source_cloud_cover": float(dataset.tags().get("MEAN_SOURCE_CLOUD", "999")),
            }
    except Exception as exc:
        return {"passed": False, "reason": f"{type(exc).__name__}: {exc}"}


def build_composite_tile(tile: str, candidates: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    fingerprint = source_products_fingerprint(candidates)
    existing = validate_tile(output, fingerprint) if output.exists() else {"passed": False}
    if existing.get("passed"):
        return existing
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_name(output.name + ".part")
    part.unlink(missing_ok=True)

    with rasterio.open(candidates[0]["bands"]["B02"]) as reference:
        transform, width, height = calculate_default_transform(
            reference.crs,
            TARGET_CRS,
            reference.width,
            reference.height,
            *reference.bounds,
            resolution=TARGET_RESOLUTION,
        )
    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": width,
        "height": height,
        "count": len(BANDS),
        "crs": TARGET_CRS,
        "transform": transform,
        "nodata": float(FLOAT_NODATA),
        "compress": "deflate",
        "predictor": 3,
        "zlevel": 6,
        "tiled": True,
        "blockxsize": BLOCK_SIZE,
        "blockysize": BLOCK_SIZE,
        "bigtiff": "YES",
        "interleave": "band",
    }

    candidate_pixels = [0 for _ in candidates]
    total_valid = 0
    total_pixels = width * height
    with ExitStack() as stack:
        warped: list[list[WarpedVRT]] = []
        for candidate in candidates:
            candidate_vrts: list[WarpedVRT] = []
            for band in BANDS:
                source = stack.enter_context(rasterio.open(candidate["bands"][band]))
                candidate_vrts.append(
                    stack.enter_context(
                        WarpedVRT(
                            source,
                            crs=TARGET_CRS,
                            transform=transform,
                            width=width,
                            height=height,
                            src_nodata=source.nodata if source.nodata is not None else 0,
                            nodata=0,
                            resampling=Resampling.nearest,
                        )
                    )
                )
            warped.append(candidate_vrts)

        with rasterio.open(part, "w", **profile) as destination:
            for index, band in enumerate(BANDS, 1):
                destination.set_band_description(index, band)
            for _, window in destination.block_windows(1):
                block_height, block_width = int(window.height), int(window.width)
                composite = np.full(
                    (len(BANDS), block_height, block_width), FLOAT_NODATA, dtype=np.float32
                )
                valid_composite = np.zeros((block_height, block_width), dtype=bool)
                for candidate_index, (candidate, candidate_vrts) in enumerate(zip(candidates, warped)):
                    dn = np.stack(
                        [vrt.read(1, window=window, out_dtype="uint16") for vrt in candidate_vrts], axis=0
                    )
                    valid_candidate = np.all(dn > 0, axis=0)
                    take = (~valid_composite) & valid_candidate
                    if not take.any():
                        continue
                    for band_index, band in enumerate(BANDS):
                        recipe = candidate["asset_metadata"][band]
                        reflectance = (
                            dn[band_index].astype(np.float32) * float(recipe["scale"])
                            + float(recipe["offset"])
                        )
                        np.clip(reflectance, 0.0, 1.0, out=reflectance)
                        composite[band_index, take] = reflectance[take]
                    valid_composite |= take
                    candidate_pixels[candidate_index] += int(take.sum())
                total_valid += int(valid_composite.sum())
                destination.write(composite, window=window)
            mean_cloud = float(np.mean([candidate["cloud_cover"] for candidate in candidates]))
            destination.update_tags(
                PIPELINE_VERSION=PIPELINE_VERSION,
                SOURCE_FINGERPRINT=fingerprint,
                SOURCE_PRODUCTS=",".join(candidate["product_id"] for candidate in candidates),
                MEAN_SOURCE_CLOUD=f"{mean_cloud:.6f}",
            )
    part.replace(output)
    result = validate_tile(output, fingerprint)
    if not result.get("passed"):
        output.unlink(missing_ok=True)
        raise ValidationError(f"{tile}: rebuilt tile failed validation: {result}")
    result["candidate_contribution_pct"] = [
        round(100.0 * count / total_pixels, 4) for count in candidate_pixels
    ]
    result["valid_pct"] = round(100.0 * total_valid / total_pixels, 4)
    return result


def next_unused_candidate(tile_catalog: dict[str, Any], used_ids: set[str]) -> dict[str, Any] | None:
    remaining = [candidate for candidate in tile_catalog["pool"] if candidate["product_id"] not in used_ids]
    if not remaining:
        return None
    used_orbits = {
        candidate.get("relative_orbit")
        for candidate in tile_catalog["pool"]
        if candidate["product_id"] in used_ids and candidate.get("relative_orbit")
    }
    different = [
        candidate
        for candidate in remaining
        if candidate.get("relative_orbit") and candidate.get("relative_orbit") not in used_orbits
    ]
    return min(different or remaining, key=lambda candidate: candidate["cloud_cover"])


def build_and_repair_tiles(
    paths: YearPaths,
    catalog: dict[str, Any],
    manifest: dict[str, Any],
    config: PipelineConfig,
    session: requests.Session,
    token_manager: TokenManager,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    tile_count = len(catalog["master_tiles"])
    for tile_index, tile in enumerate(catalog["master_tiles"], 1):
        tile_entry = manifest["tiles"][tile]
        candidates = tile_entry["candidates"]
        output = paths.work_dir / f"{tile}.tif"
        while True:
            log(
                f"{paths.year} tile build {tile_index}/{tile_count} {tile}: "
                f"compositing {len(candidates)} product(s)"
            )
            result = build_composite_tile(tile, candidates, output)
            nodata_pct = float(result.get("nodata_pct", 100.0))
            if nodata_pct <= config.tile_nodata_max_pct:
                break
            if len(candidates) >= config.max_candidates:
                log(
                    f"{paths.year} {tile}: {nodata_pct:.3f}% tile nodata remains after "
                    f"{len(candidates)} products; exact boundary QA will decide"
                )
                break
            used = {candidate["product_id"] for candidate in candidates}
            replacement = next_unused_candidate(catalog["tiles"][tile], used)
            if replacement is None:
                log(f"{paths.year} {tile}: no additional candidates exist for footprint repair")
                break
            new_entry = None
            while replacement is not None and new_entry is None:
                log(
                    f"{paths.year} {tile}: actual nodata {nodata_pct:.3f}% exceeds "
                    f"{config.tile_nodata_max_pct:.3f}%; downloading repair candidate "
                    f"{replacement['product_id']}"
                )
                try:
                    new_entry = ensure_candidate_downloaded(
                        paths, replacement, session, token_manager, config
                    )
                except DownloadError as exc:
                    log(
                        f"{paths.year} {tile}: repair candidate {replacement['product_id']} "
                        f"failed; trying another: {exc}"
                    )
                    used.add(replacement["product_id"])
                    replacement = next_unused_candidate(catalog["tiles"][tile], used)
            if new_entry is None:
                log(f"{paths.year} {tile}: every remaining footprint-repair candidate failed")
                break
            candidates.append(new_entry)
            tile_entry["candidates"] = candidates
            tile_entry["footprint_repair_candidates_added"] = len(candidates) - config.top_n
            tile_entry["n_unique_orbits_all_candidates"] = len(
                {entry.get("relative_orbit") for entry in candidates if entry.get("relative_orbit")}
            )
            manifest["updated_at"] = utc_now()
            atomic_write_json(paths.manifest, manifest)
            output.unlink(missing_ok=True)
        result.update(
            {
                "tile": tile,
                "path": str(output.resolve()),
                "mean_source_cloud_cover": float(
                    np.mean([candidate["cloud_cover"] for candidate in candidates])
                ),
                "product_ids": [candidate["product_id"] for candidate in candidates],
            }
        )
        tile_entry["composite_validation"] = result
        atomic_write_json(paths.manifest, manifest)
        results.append(result)
    return results


def aligned_mosaic_grid(tile_results: list[dict[str, Any]]) -> tuple[Any, int, int]:
    bounds = [result["bounds"] for result in tile_results]
    left = math.floor(min(item[0] for item in bounds) / TARGET_RESOLUTION) * TARGET_RESOLUTION
    bottom = math.floor(min(item[1] for item in bounds) / TARGET_RESOLUTION) * TARGET_RESOLUTION
    right = math.ceil(max(item[2] for item in bounds) / TARGET_RESOLUTION) * TARGET_RESOLUTION
    top = math.ceil(max(item[3] for item in bounds) / TARGET_RESOLUTION) * TARGET_RESOLUTION
    width = int(round((right - left) / TARGET_RESOLUTION))
    height = int(round((top - bottom) / TARGET_RESOLUTION))
    return from_origin(left, top, TARGET_RESOLUTION, TARGET_RESOLUTION), width, height


def mosaic_fingerprint(tile_results: list[dict[str, Any]]) -> str:
    payload = [
        {
            "tile": result["tile"],
            "product_ids": result["product_ids"],
            "path": result["path"],
            "size": Path(result["path"]).stat().st_size,
            "mtime_ns": Path(result["path"]).stat().st_mtime_ns,
        }
        for result in tile_results
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def quick_mosaic_structure(path: Path, require_cog_layout: bool) -> dict[str, Any]:
    try:
        with rasterio.open(path) as dataset:
            layout = dataset.tags(ns="IMAGE_STRUCTURE").get("LAYOUT")
            overviews = dataset.overviews(1)
            is_tiled = bool(dataset.profile.get("tiled", False))
            passed = (
                dataset.count == len(BANDS)
                and set(dataset.dtypes) == {"float32"}
                and dataset.crs == TARGET_CRS
                and dataset.nodata == float(FLOAT_NODATA)
                and is_tiled
                and bool(overviews)
                and (not require_cog_layout or layout == "COG")
            )
            return {
                "passed": passed,
                "driver": dataset.driver,
                "is_tiled": is_tiled,
                "block_shapes": [list(item) for item in dataset.block_shapes],
                "overviews": overviews,
                "layout": layout,
                "width": dataset.width,
                "height": dataset.height,
                "count": dataset.count,
                "dtypes": list(dataset.dtypes),
                "crs": str(dataset.crs),
                "nodata": dataset.nodata,
            }
    except Exception as exc:
        return {"passed": False, "reason": f"{type(exc).__name__}: {exc}"}


def build_resumable_staging(paths: YearPaths, tile_results: list[dict[str, Any]]) -> str:
    paths.mosaic_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = mosaic_fingerprint(tile_results)
    staging_meta = read_json(paths.staging_meta, {}) or {}
    if (
        paths.staging.exists()
        and staging_meta.get("fingerprint") == fingerprint
        and staging_meta.get("complete") is True
    ):
        log(f"{paths.year}: reusing completed merge staging raster")
        return fingerprint

    transform, width, height = aligned_mosaic_grid(tile_results)
    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": width,
        "height": height,
        "count": len(BANDS),
        "crs": TARGET_CRS,
        "transform": transform,
        "nodata": float(FLOAT_NODATA),
        "compress": "deflate",
        "predictor": 3,
        "zlevel": 6,
        "tiled": True,
        "blockxsize": BLOCK_SIZE,
        "blockysize": BLOCK_SIZE,
        "bigtiff": "YES",
        "interleave": "pixel",
    }
    expected_state = {
        "pipeline_version": PIPELINE_VERSION,
        "fingerprint": fingerprint,
        "width": width,
        "height": height,
    }
    state = read_json(paths.merge_state, {}) or {}
    can_resume = (
        paths.staging.exists()
        and all(state.get(key) == value for key, value in expected_state.items())
    )
    if not can_resume:
        paths.staging.unlink(missing_ok=True)
        state = {**expected_state, "next_block": 0, "updated_at": utc_now()}
        with rasterio.open(paths.staging, "w", **profile) as destination:
            for index, band in enumerate(BANDS, 1):
                destination.set_band_description(index, band)
            destination.update_tags(PIPELINE_VERSION=PIPELINE_VERSION, SOURCE_FINGERPRINT=fingerprint)
        atomic_write_json(paths.merge_state, state)

    ordered = sorted(tile_results, key=lambda result: result["mean_source_cloud_cover"])
    start_block = int(state.get("next_block", 0))
    log(
        f"{paths.year}: merging {len(ordered)} tile composites into "
        f"{width}x{height} staging raster (resume block {start_block})"
    )
    with ExitStack() as stack:
        vrts: list[tuple[WarpedVRT, tuple[float, float, float, float]]] = []
        for result in ordered:
            source = stack.enter_context(rasterio.open(result["path"]))
            vrts.append(
                (
                    stack.enter_context(
                        WarpedVRT(
                            source,
                            crs=TARGET_CRS,
                            transform=transform,
                            width=width,
                            height=height,
                            src_nodata=float(FLOAT_NODATA),
                            nodata=float(FLOAT_NODATA),
                            resampling=Resampling.nearest,
                        )
                    ),
                    tuple(result["bounds"]),
                )
            )
        with rasterio.open(paths.staging, "r+") as destination:
            windows = list(destination.block_windows(1))
            total_blocks = len(windows)
            for block_index, (_, window) in enumerate(windows):
                if block_index < start_block:
                    continue
                out = np.full(
                    (len(BANDS), int(window.height), int(window.width)), FLOAT_NODATA, dtype=np.float32
                )
                occupied = np.zeros((int(window.height), int(window.width)), dtype=bool)
                block_left, block_bottom, block_right, block_top = window_bounds(window, transform)
                for vrt, source_bounds in vrts:
                    source_left, source_bottom, source_right, source_top = source_bounds
                    if (
                        source_right <= block_left
                        or source_left >= block_right
                        or source_top <= block_bottom
                        or source_bottom >= block_top
                    ):
                        continue
                    data = vrt.read(window=window, out_dtype="float32")
                    valid = np.all(data != float(FLOAT_NODATA), axis=0)
                    take = (~occupied) & valid
                    if take.any():
                        out[:, take] = data[:, take]
                        occupied |= take
                destination.write(out, window=window)
                if block_index % 20 == 0 or block_index + 1 == total_blocks:
                    state["next_block"] = block_index + 1
                    state["updated_at"] = utc_now()
                    atomic_write_json(paths.merge_state, state)
                    log(
                        f"{paths.year}: merge block {block_index + 1}/{total_blocks} "
                        f"({100.0 * (block_index + 1) / total_blocks:.1f}%)"
                    )

    atomic_write_json(
        paths.staging_meta,
        {
            **expected_state,
            "complete": True,
            "completed_at": utc_now(),
            "tile_count": len(tile_results),
        },
    )
    return fingerprint


def translate_to_cog(paths: YearPaths, config: PipelineConfig, fingerprint: str) -> None:
    existing_structure = quick_mosaic_structure(paths.cog, config.strict_cog) if paths.cog.exists() else {}
    if existing_structure.get("passed"):
        with rasterio.open(paths.cog) as dataset:
            if dataset.tags().get("SOURCE_FINGERPRINT") == fingerprint:
                log(f"{paths.year}: existing final COG matches current tile sources")
                return

    part = paths.cog.with_name(paths.cog.name + ".part")
    part.unlink(missing_ok=True)
    if config.strict_cog:
        with rasterio.Env() as environment:
            if "COG" not in environment.drivers():
                raise PipelineError("this GDAL/rasterio build does not provide the COG driver")
        log(
            f"{paths.year}: translating staging raster with GDAL COG driver "
            f"(compression={config.cog_compression}, level={config.cog_level}, "
            f"threads={config.gdal_threads})"
        )
        heartbeat_stop = threading.Event()

        def report_translation_progress() -> None:
            started = time.monotonic()
            while not heartbeat_stop.wait(60):
                size_gb = part.stat().st_size / 1e9 if part.exists() else 0.0
                elapsed_minutes = (time.monotonic() - started) / 60
                log(
                    f"{paths.year}: COG translation heartbeat - output={size_gb:.2f} GB, "
                    f"elapsed={elapsed_minutes:.1f} min"
                )

        heartbeat = threading.Thread(
            target=report_translation_progress,
            name=f"cog-heartbeat-{paths.year}",
            daemon=True,
        )
        heartbeat.start()
        try:
            raster_copy(
                paths.staging,
                part,
                driver="COG",
                BLOCKSIZE=str(BLOCK_SIZE),
                COMPRESS=config.cog_compression,
                LEVEL=str(config.cog_level),
                PREDICTOR="FLOATING_POINT",
                BIGTIFF="YES",
                OVERVIEWS="AUTO",
                RESAMPLING="NEAREST",
                NUM_THREADS=config.gdal_threads,
            )
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=5)
    else:
        log(f"{paths.year}: creating application-optimized tiled GeoTIFF")
        shutil.copy2(paths.staging, part)
        with rasterio.open(part, "r+") as destination:
            destination.build_overviews(OVERVIEW_FACTORS, Resampling.nearest)
            destination.update_tags(ns="rio_overview", resampling="nearest")
    if not config.strict_cog:
        with rasterio.open(part, "r+") as destination:
            destination.update_tags(PIPELINE_VERSION=PIPELINE_VERSION, SOURCE_FINGERPRINT=fingerprint)
    structure = quick_mosaic_structure(part, config.strict_cog)
    if not structure.get("passed"):
        part.unlink(missing_ok=True)
        raise ValidationError(f"translated COG failed structural validation: {structure}")
    part.replace(paths.cog)


def load_boundary(path: Path):
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("type") == "FeatureCollection":
        geometries = [shape(feature["geometry"]) for feature in body.get("features", [])]
    elif body.get("type") == "Feature":
        geometries = [shape(body["geometry"])]
    else:
        geometries = [shape(body)]
    if not geometries:
        raise PipelineError(f"boundary contains no geometry: {path}")
    return unary_union(geometries)


def make_preview(cog_path: Path, output: Path) -> dict[str, Any]:
    with rasterio.open(cog_path) as dataset:
        max_dimension = 1600
        scale = max(dataset.width / max_dimension, dataset.height / max_dimension, 1.0)
        width = max(1, int(dataset.width / scale))
        height = max(1, int(dataset.height / scale))
        rgb = dataset.read([3, 2, 1], out_shape=(3, height, width), resampling=Resampling.nearest)
        valid = np.all(rgb != dataset.nodata, axis=0)
        valid_values = rgb[:, valid]
        low, high = np.percentile(valid_values, [2, 98]) if valid_values.size else (0.0, 1.0)
        rgb[:, ~valid] = 0.0
        stretched = np.clip((rgb - low) / (high - low + 1e-6), 0.0, 1.0)
        image = Image.fromarray(np.transpose((stretched * 255).astype(np.uint8), (1, 2, 0)), "RGB")
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output)
        return {"path": str(output.resolve()), "width": width, "height": height, "p2": float(low), "p98": float(high)}


def validate_final_cog(paths: YearPaths, config: PipelineConfig) -> dict[str, Any]:
    structure = quick_mosaic_structure(paths.cog, config.strict_cog)
    if not structure.get("passed"):
        return {"passed": False, "structure": structure, "errors": ["COG structure validation failed"]}

    boundary_wgs84 = load_boundary(config.boundary_path)
    samples: list[np.ndarray] = []
    boundary_pixels = 0
    nodata_pixels = 0
    non_finite = 0
    out_of_range = 0
    band_min = np.full(len(BANDS), np.inf, dtype=np.float64)
    band_max = np.full(len(BANDS), -np.inf, dtype=np.float64)
    with rasterio.open(paths.cog) as dataset:
        transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
        boundary = shapely_transform(transformer.transform, boundary_wgs84)
        boundary_bbox = box(*boundary.bounds)
        boundary_mapping = mapping(boundary)
        for _, window in dataset.block_windows(1):
            block_bbox = box(*window_bounds(window, dataset.transform))
            if not block_bbox.intersects(boundary_bbox):
                continue
            inside = geometry_mask(
                [boundary_mapping],
                out_shape=(int(window.height), int(window.width)),
                transform=window_transform(window, dataset.transform),
                invert=True,
                all_touched=False,
            )
            if not inside.any():
                continue
            data = dataset.read(window=window)
            valid = inside & np.all(data != dataset.nodata, axis=0)
            boundary_pixels += int(inside.sum())
            nodata_pixels += int((inside & ~valid).sum())
            if not valid.any():
                continue
            values = data[:, valid]
            finite = np.isfinite(values)
            non_finite += int((~finite).sum())
            out_of_range += int(((values < 0.0) | (values > 1.0001)).sum())
            for band_index in range(len(BANDS)):
                finite_values = values[band_index, finite[band_index]]
                if finite_values.size:
                    band_min[band_index] = min(band_min[band_index], float(finite_values.min()))
                    band_max[band_index] = max(band_max[band_index], float(finite_values.max()))
            # Deterministic bounded sample: at most 32 pixels from each block.
            take_count = min(32, values.shape[1])
            indices = np.linspace(0, values.shape[1] - 1, take_count, dtype=int)
            samples.append(values[:, indices])

    nodata_pct = 100.0 * nodata_pixels / boundary_pixels if boundary_pixels else 100.0
    sample = np.concatenate(samples, axis=1) if samples else np.empty((len(BANDS), 0), dtype=np.float32)
    band_stats: dict[str, Any] = {}
    for index, band in enumerate(BANDS):
        values = sample[index]
        band_stats[band] = {
            "min": None if not np.isfinite(band_min[index]) else round(float(band_min[index]), 6),
            "max": None if not np.isfinite(band_max[index]) else round(float(band_max[index]), 6),
            "mean_sample": None if not values.size else round(float(values.mean()), 6),
            "median_sample": None if not values.size else round(float(np.median(values)), 6),
        }
    vegetation: dict[str, Any]
    if sample.shape[1]:
        b04 = sample[BANDS.index("B04")]
        b08 = sample[BANDS.index("B08")]
        ndvi = (b08 - b04) / (b08 + b04 + 1e-10)
        vegetation_b08 = b08[ndvi >= 0.45]
        vegetation_median = float(np.median(vegetation_b08)) if vegetation_b08.size else None
        vegetation = {
            "sample_count": int(vegetation_b08.size),
            "b08_median_reflectance": None if vegetation_median is None else round(vegetation_median, 6),
            "expected_typical_range": [0.15, 0.40],
            "accepted_guardrail": [0.12, 0.60],
            "passed": vegetation_median is not None and 0.12 <= vegetation_median <= 0.60,
        }
    else:
        vegetation = {"sample_count": 0, "passed": False}

    errors: list[str] = []
    if boundary_pixels == 0:
        errors.append("official boundary does not intersect mosaic")
    if nodata_pct > config.aoi_nodata_max_pct:
        errors.append(
            f"nodata inside official boundary is {nodata_pct:.4f}% > {config.aoi_nodata_max_pct:.4f}%"
        )
    if non_finite:
        errors.append(f"found {non_finite} non-finite reflectance values")
    if out_of_range:
        errors.append(f"found {out_of_range} reflectance values outside [0,1]")
    if not vegetation.get("passed"):
        errors.append("vegetation B08 reflectance sanity check failed")

    preview = make_preview(paths.cog, paths.mosaic_dir / "preview.png")
    report = {
        "pipeline_version": PIPELINE_VERSION,
        "period_id": f"{paths.year}_summer",
        "generated_at": utc_now(),
        "passed": not errors,
        "errors": errors,
        "structure": structure,
        "coverage": {
            "boundary_pixels": boundary_pixels,
            "nodata_pixels": nodata_pixels,
            "nodata_pct": round(nodata_pct, 6),
            "max_allowed_nodata_pct": config.aoi_nodata_max_pct,
        },
        "reflectance": {
            "non_finite_values": non_finite,
            "out_of_range_values": out_of_range,
            "band_stats": band_stats,
            "vegetation_sanity": vegetation,
        },
        "preview": preview,
    }
    atomic_write_json(paths.qa_report, report)
    return report


def already_complete(paths: YearPaths, config: PipelineConfig) -> bool:
    report = read_json(paths.qa_report, {}) or {}
    if not (paths.cog.exists() and report.get("passed") is True):
        return False
    structure = quick_mosaic_structure(paths.cog, config.strict_cog)
    return bool(structure.get("passed"))


def write_metadata(
    paths: YearPaths,
    catalog: dict[str, Any],
    manifest: dict[str, Any],
    qa: dict[str, Any],
) -> None:
    metadata = {
        "pipeline_version": PIPELINE_VERSION,
        "period_id": f"{paths.year}_summer",
        "year": paths.year,
        "date_range": f"01.06.{paths.year} - 31.08.{paths.year}",
        "source": SOURCE_NAME,
        "provider": "Copernicus Data Space Ecosystem",
        "collection": COLLECTION,
        "product": "Sentinel-2 MSI Level-2A BOA surface reflectance",
        "bands": BANDS,
        "storage": "float32 physical reflectance",
        "nodata": float(FLOAT_NODATA),
        "crs": str(TARGET_CRS),
        "resolution_m": TARGET_RESOLUTION,
        "tile_count": len(catalog["master_tiles"]),
        "products_used": sum(len(tile["candidates"]) for tile in manifest["tiles"].values()),
        "manifest": str(paths.manifest.resolve()),
        "catalog": str(paths.catalog.resolve()),
        "cog": str(paths.cog.resolve()),
        "qa_report": str(paths.qa_report.resolve()),
        "qa_passed": qa.get("passed") is True,
        "completed_at": utc_now(),
    }
    atomic_write_json(paths.metadata, metadata)


def cleanup_after_qa(paths: YearPaths, config: PipelineConfig) -> None:
    # Staging is a derived duplicate of the validated COG and is always safe to
    # remove. Raw data remains by default, exactly as required by the QA guide.
    paths.staging.unlink(missing_ok=True)
    paths.merge_state.unlink(missing_ok=True)
    if config.cleanup_work_after_qa and paths.work_dir.exists():
        shutil.rmtree(paths.work_dir)
    if config.cleanup_raw_after_qa and paths.raw_dir.exists():
        shutil.rmtree(paths.raw_dir)


def run_year(
    year: int,
    config: PipelineConfig,
    session: requests.Session,
    token_manager: TokenManager,
    reference_tiles: list[str],
) -> None:
    paths = YearPaths(year=year, data_root=config.data_root)
    paths.mosaic_dir.mkdir(parents=True, exist_ok=True)
    if already_complete(paths, config):
        log(f"{year}: QA-passed COG already exists; skipping entire year")
        update_state(paths, "complete", "complete", cog=str(paths.cog))
        return

    update_state(paths, "catalog", "running")
    catalog = prepare_catalog(paths, config, session, reference_tiles)
    update_state(paths, "download", "running")
    manifest = download_initial_candidates(paths, catalog, config, session, token_manager)
    update_state(paths, "tile_composites", "running")
    tile_results = build_and_repair_tiles(
        paths, catalog, manifest, config, session, token_manager
    )
    update_state(paths, "merge", "running")
    fingerprint = build_resumable_staging(paths, tile_results)
    update_state(paths, "cog_translate", "running")
    translate_to_cog(paths, config, fingerprint)
    update_state(paths, "qa", "running")
    qa = validate_final_cog(paths, config)
    if not qa.get("passed"):
        update_state(paths, "qa", "failed", errors=qa.get("errors", []))
        raise ValidationError(f"{year}: final COG failed QA: {qa.get('errors')}")
    write_metadata(paths, catalog, manifest, qa)
    cleanup_after_qa(paths, config)
    update_state(
        paths,
        "complete",
        "complete",
        cog=str(paths.cog.resolve()),
        qa_report=str(paths.qa_report.resolve()),
    )
    log(f"{year}: COMPLETE - COG and all automated QA checks passed")


def resolve_default_data_root() -> Path:
    explicit = os.environ.get("S2_DATA_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    mosaics_dir = os.environ.get("MOSAICS_DIR", "").strip()
    if mosaics_dir:
        return Path(mosaics_dir).expanduser().parent
    return PROJECT_ROOT / "data" / "sentinel2_pipeline"


def disk_preflight(config: PipelineConfig) -> None:
    config.data_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(config.data_root)
    free_gb = usage.free / 1e9
    log(f"Data root: {config.data_root.resolve()} ({free_gb:.1f} GB free)")
    if free_gb < 20:
        raise PipelineError(
            f"only {free_gb:.1f} GB free at {config.data_root}; at least 20 GB is required to start safely"
        )
    if not config.boundary_path.exists():
        raise PipelineError(f"official boundary file is missing: {config.boundary_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="+", type=int, default=[2018, 2019, 2020, 2021, 2022])
    parser.add_argument("--data-root", type=Path, default=resolve_default_data_root())
    parser.add_argument(
        "--boundary",
        type=Path,
        default=PROJECT_ROOT / "frontend" / "public" / "turkestan_boundary.geojson",
    )
    parser.add_argument("--reference-year", type=int, default=2025)
    parser.add_argument("--cloud-max", type=float, default=40.0)
    parser.add_argument("--fallback-cloud-max", type=float, default=60.0)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--max-candidates", type=int, default=6)
    parser.add_argument("--download-attempts", type=int, default=8)
    parser.add_argument("--http-attempts", type=int, default=8)
    parser.add_argument("--tile-nodata-max-pct", type=float, default=0.5)
    parser.add_argument("--aoi-nodata-max-pct", type=float, default=0.5)
    parser.add_argument("--gdal-threads", default="4")
    parser.add_argument(
        "--cog-compression",
        choices=["ZSTD", "DEFLATE", "LZW"],
        default="ZSTD",
        help="GDAL COG compression (ZSTD is substantially faster than DEFLATE)",
    )
    parser.add_argument("--cog-level", type=int, default=1)
    parser.add_argument("--keep-going", action="store_true", help="continue later years after a failed year")
    parser.add_argument("--cleanup-raw-after-qa", action="store_true")
    parser.add_argument("--cleanup-work-after-qa", action="store_true")
    cog_group = parser.add_mutually_exclusive_group()
    cog_group.add_argument("--strict-cog", dest="strict_cog", action="store_true", default=True)
    cog_group.add_argument("--application-geotiff", dest="strict_cog", action="store_false")
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    years = sorted(set(args.years))
    if not years or any(year < 2017 or year >= datetime.now().year for year in years):
        raise SystemExit("years must be complete Sentinel-2 seasons from 2017 through last year")
    if args.top_n < 1 or args.max_candidates < args.top_n:
        raise SystemExit("--max-candidates must be >= --top-n >= 1")

    username = os.environ.get("CDSE_USERNAME", "").strip()
    password = os.environ.get("CDSE_PASSWORD", "").strip()
    if not username or not password:
        raise SystemExit("CDSE_USERNAME and CDSE_PASSWORD are required (password grant, account without MFA)")

    config = PipelineConfig(
        data_root=args.data_root.expanduser().resolve(),
        boundary_path=args.boundary.expanduser().resolve(),
        reference_year=args.reference_year,
        cloud_max=args.cloud_max,
        fallback_cloud_max=args.fallback_cloud_max,
        top_n=args.top_n,
        max_candidates=args.max_candidates,
        download_attempts=args.download_attempts,
        http_attempts=args.http_attempts,
        tile_nodata_max_pct=args.tile_nodata_max_pct,
        aoi_nodata_max_pct=args.aoi_nodata_max_pct,
        gdal_threads=str(args.gdal_threads),
        cog_compression=args.cog_compression,
        cog_level=args.cog_level,
        strict_cog=args.strict_cog,
        cleanup_raw_after_qa=args.cleanup_raw_after_qa,
        cleanup_work_after_qa=args.cleanup_work_after_qa,
    )
    disk_preflight(config)
    session = requests.Session()
    session.headers.update({"User-Agent": f"GeoAI-TKO/{PIPELINE_VERSION}"})
    token_manager = TokenManager(session, username, password, config.http_attempts)
    # Authenticate before a weekend-long run so wrong credentials or MFA fail
    # immediately instead of after the public catalog stage.
    token_manager.get()
    reference_tiles = prepare_reference_tiles(config, session)

    failures: dict[int, str] = {}
    for year in years:
        try:
            log("=" * 72)
            log(f"STARTING {year}_summer")
            run_year(year, config, session, token_manager, reference_tiles)
        except Exception as exc:
            failures[year] = f"{type(exc).__name__}: {exc}"
            paths = YearPaths(year=year, data_root=config.data_root)
            update_state(paths, "failed", "failed", error=failures[year])
            log(f"{year}: FAILED - {failures[year]}")
            if not args.keep_going:
                break

    if failures:
        log(f"Run finished with failures: {failures}")
        return 1
    log(f"ALL REQUESTED YEARS COMPLETE: {years}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
