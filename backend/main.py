"""
GeoAI-TKO — FastAPI Backend v4
================================
Primary data source: per-period COG mosaics (see PERIODS registry below).
  7 bands: B02=1 B03=2 B04=3 B05=4 B08=5 B8A=6 B11=7   CRS EPSG:32641, 10m
Fallback: src/processing/s2_work/*.tif (per-scene tiles, merged on the fly; default period only)

Endpoints:
  GET  /health                          service check
  GET  /api/periods                     list of available periods for the UI selector
  GET  /tiles/{layer}/{z}/{x}/{y}.png?period=   XYZ tiles  (ndvi / ndwi / ndre / ndmi / bsi)
  GET  /tiles/change/{index}/{z}/{x}/{y}.png?period_before=&period_after=  change-detection XYZ tiles
  GET  /tiles/forecast/{index}/{target_year}/{z}/{x}/{y}.png  linear-trend forecast tiles
  GET  /api/pixel?lat=&lon=&period=     per-pixel spectral values + indices
  GET  /api/forecast/point?lat=&lon=&index=&target_year=  point trend forecast
  POST /api/zone_stats                  zonal stats (indices + LULC) for a drawn polygon
  POST /api/zone_timeseries             multi-year zonal index history
  POST /api/change_stats                change-detection zonal stats (index deltas + ML transition matrix)
  GET  /api/change_overview             precomputed change-detection stats for the whole oblast boundary
  POST /api/transect                    index profile (line profile) along a drawn line
  POST /api/zone_report                 structured Groq report for a drawn zone (PDF built on frontend)
  POST /api/analyze                     AI interpretation (Groq / DeepSeek / local fallback)
  GET  /metadata                        region + layer metadata (default period only)
  GET  /data/...                        static data files

Run:
  uvicorn backend.main:app --reload --port 8000
"""

import asyncio, functools, io, json, logging, math, os, threading, time, traceback, uuid
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
# Load project-root .env explicitly so it works regardless of CWD
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, Query, Request, Response, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

try:
    from .account_api import create_account_router
    from .backup_accounts import backup_status
    from .account_mailer import AccountMailer
    from .account_store import AccountStore
    from .rate_limit import SlidingWindowRateLimiter
    from .monitoring import MonitoringService
    from .cdse_catalog import CdseSceneCatalog, SceneSearchError
except ImportError:  # supports `python backend/main.py`
    from account_api import create_account_router
    from backup_accounts import backup_status
    from account_mailer import AccountMailer
    from account_store import AccountStore
    from rate_limit import SlidingWindowRateLimiter
    from monitoring import MonitoringService
    from cdse_catalog import CdseSceneCatalog, SceneSearchError

# ── Optional geo libs ─────────────────────────────────────────────
try:
    import numpy as np
    from PIL import Image
    ARRAY_LIBS_OK = True
except ImportError as e:
    np = None
    Image = None
    ARRAY_LIBS_OK = False
    print(f"[warning] NumPy / Pillow not available: {e}")

try:
    from rio_tiler.io import Reader
    from rio_tiler.errors import TileOutsideBounds
    TILER_OK = ARRAY_LIBS_OK
except ImportError as e:
    TILER_OK = False
    print(f"[warning] rio-tiler not available: {e}")

try:
    import rasterio
    from rasterio.windows import from_bounds as window_from_bounds, Window
    from rasterio.features import geometry_mask
    from pyproj import Transformer
    RASTERIO_OK = ARRAY_LIBS_OK
except ImportError:
    RASTERIO_OK = False
    print("[warning] rasterio not available - raster analysis disabled")

try:
    from openai import OpenAI
    AI_OK = True
except ImportError:
    AI_OK = False

try:
    import pickle
    import sklearn  # noqa: F401  needed so pickle.load can resolve the saved estimator
    from scipy.ndimage import uniform_filter
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False
    print("[warning] scikit-learn not available - ML land-cover classification disabled")

# ── Paths ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR  = BASE_DIR / "data"
S2_DIR    = BASE_DIR / "src" / "processing" / "s2_work"
APP_DB_PATH = Path(os.getenv("APP_DB_PATH", str(BASE_DIR / "data" / "geoai_tko.sqlite3")))
ACCOUNT_BACKUP_DIR = Path(
    os.getenv("ACCOUNT_BACKUP_DIR", str(BASE_DIR / "backups" / "accounts"))
)
BACKUP_MAX_AGE_HOURS = float(os.getenv("BACKUP_MAX_AGE_HOURS", "36"))
BACKUP_HEALTH_REQUIRED = os.getenv("BACKUP_HEALTH_REQUIRED", "false").lower() in {
    "1", "true", "yes"
}
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()

LOGGER = logging.getLogger("geoai_tko")
LOGGER.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
if not LOGGER.handlers:
    log_handler = logging.StreamHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(log_handler)
LOGGER.propagate = False


def log_event(level: int, event: str, **fields) -> None:
    LOGGER.log(
        level,
        json.dumps(
            {"event": event, **fields}, ensure_ascii=False, separators=(",", ":")
        ),
    )
MOSAICS_DIR  = Path(os.getenv("MOSAICS_DIR", r"D:\data\mosaics"))
LULC_MODEL_PATH = Path(os.getenv("LULC_MODEL_PATH", r"D:\data\classifiers\lulc_classifier.pkl"))
LULC_MODEL_V2_PATH = Path(os.getenv("LULC_MODEL_V2_PATH", r"D:\data\classifiers\lulc_classifier_v3.pkl"))
WORLDCOVER_PATH = Path(os.getenv("WORLDCOVER_PATH", r"D:\data\reference\esa_worldcover_turkestan.tif"))
S2_SOURCE = "Copernicus Data Space Ecosystem"
S2_PRODUCT = "Sentinel-2 MSI Level-2A BOA surface reflectance"
S2_BANDS = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11"]

# ── Period registry ──────────────────────────────────────────────
# Independent, non-comparable map states — each period is viewed on its own.
# "storage" records the physical pixel format of that period's COG, since the
# two mosaics are NOT stored the same way:
#   "dn"          — raw uint16 digital numbers, nodata=0, reflectance = DN/10000
#   "reflectance" — physical float32 reflectance already in 0..1, nodata=-9999
# 2023_summer now points at the CDSE-rebuilt mosaic (2023_summer_cdse/
# s2_mosaic_cog.tif, float32 reflectance, QA-passed) instead of the original
# Planetary Computer uint16 file — both periods are now sourced from CDSE
# with identical processing, so cross-period comparison is no longer
# masked by provider-specific preprocessing differences (see
# D:\data\mosaics\2023_summer_cdse\metadata.json for full QA record). The
# old PC-based files (2023_summer/s2_mosaic_cog.tif, s2_mosaic_cog_v2.tif)
# are left on disk untouched as an archival reference, just no longer read.
PERIODS = {
    "2023_summer": {
        "label": "Лето 2023",
        "date_range": "01.06.2023 – 31.08.2023",
        "cog_path": MOSAICS_DIR / "2023_summer_cdse" / "s2_mosaic_cog.tif",
        "storage": "reflectance",
    },
    "2024_summer": {
        "label": "Лето 2024",
        "date_range": "01.06.2024 – 31.08.2024",
        "cog_path": MOSAICS_DIR / "2024_summer" / "s2_mosaic_cog.tif",
        "storage": "reflectance",
    },
    "2025_summer": {
        "label": "Лето 2025",
        "date_range": "01.06.2025 – 31.08.2025",
        "cog_path": MOSAICS_DIR / "2025_summer" / "s2_mosaic_cog.tif",
        "storage": "reflectance",
    },
}
DEFAULT_PERIOD = "2025_summer"
_REFLECTANCE_NODATA = -9999
MAX_ANALYSIS_PIXELS = int(os.getenv("MAX_ANALYSIS_PIXELS", "2000000"))
MAX_GEOMETRY_VERTICES = int(os.getenv("MAX_GEOMETRY_VERTICES", "10000"))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "2000000"))
MAX_CONCURRENT_ANALYSES = max(1, int(os.getenv("MAX_CONCURRENT_ANALYSES", "2")))
MAX_FORECAST_HORIZON_YEARS = max(1, int(os.getenv("MAX_FORECAST_HORIZON_YEARS", "5")))
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in {"1", "true", "yes"}
RATE_LIMIT_WINDOW_SECONDS = max(1, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")))
API_RATE_LIMIT = max(1, int(os.getenv("API_RATE_LIMIT", "120")))
ANALYSIS_RATE_LIMIT = max(1, int(os.getenv("ANALYSIS_RATE_LIMIT", "20")))
TILE_RATE_LIMIT = max(1, int(os.getenv("TILE_RATE_LIMIT", "600")))
TILE_CACHE_SECONDS = max(60, int(os.getenv("TILE_CACHE_SECONDS", "86400")))
CDSE_CATALOG_ENABLED = os.getenv("CDSE_CATALOG_ENABLED", "true").lower() in {"1", "true", "yes"}
CDSE_CATALOG_TIMEOUT_SECONDS = max(1.0, min(30.0, float(os.getenv("CDSE_CATALOG_TIMEOUT_SECONDS", "12"))))
CDSE_CATALOG_CACHE_SECONDS = max(0, min(3600, int(os.getenv("CDSE_CATALOG_CACHE_SECONDS", "300"))))
MAX_CONCURRENT_CATALOG_SEARCHES = max(1, min(16, int(os.getenv("MAX_CONCURRENT_CATALOG_SEARCHES", "4"))))
AI_TIMEOUT_SECONDS = max(1.0, float(os.getenv("AI_TIMEOUT_SECONDS", "15")))
AI_MAX_RETRIES = max(0, min(2, int(os.getenv("AI_MAX_RETRIES", "0"))))
MONITORING_SCHEDULER_ENABLED = os.getenv("MONITORING_SCHEDULER_ENABLED", "false").lower() in {"1", "true", "yes"}
MONITORING_INTERVAL_SECONDS = max(60, int(os.getenv("MONITORING_INTERVAL_SECONDS", str(6 * 60 * 60))))
ENABLE_DEMO_DATA = os.getenv("ENABLE_DEMO_DATA", "false").lower() in {"1", "true", "yes"}
ENABLE_CHANGE_OVERVIEW = os.getenv("ENABLE_CHANGE_OVERVIEW", "false").lower() in {"1", "true", "yes"}
_ANALYSIS_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_ANALYSES)
_CATALOG_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_CATALOG_SEARCHES)
_API_RATE_LIMITER = SlidingWindowRateLimiter()
CDSE_SCENE_CATALOG = CdseSceneCatalog(
    enabled=CDSE_CATALOG_ENABLED,
    timeout_seconds=CDSE_CATALOG_TIMEOUT_SECONDS,
    cache_seconds=CDSE_CATALOG_CACHE_SECONDS,
)

REGION_FALLBACK_BOUNDS = [40.31, 65.36, 46.46, 71.36]  # south, west, north, east


def limit_analysis(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        if not _ANALYSIS_SEMAPHORE.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="Сервер занят другим анализом — повторите запрос позже")
        try:
            return func(*args, **kwargs)
        finally:
            _ANALYSIS_SEMAPHORE.release()
    return wrapped


def ai_client(api_key: str, base_url: str):
    """Create a bounded external AI client so requests cannot occupy workers indefinitely."""
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=AI_TIMEOUT_SECONDS,
        max_retries=AI_MAX_RETRIES,
    )

# Kept for the /health and /metadata endpoints, which stay tied to the
# default period (not part of the period switcher).
COG_PATH = PERIODS[DEFAULT_PERIOD]["cog_path"]


def resolve_period(period_id: str) -> dict:
    cfg = PERIODS.get(period_id)
    if cfg is None:
        raise HTTPException(status_code=400, detail=f"Неизвестный период: {period_id}")
    return cfg


def period_nodata_mask(data: "np.ndarray", period: dict) -> "np.ndarray":
    """bool array (rows, cols) — True where all bands are nodata, per the period's storage format."""
    if period["storage"] == "reflectance":
        return np.all(data == _REFLECTANCE_NODATA, axis=0)
    return np.all(data == 0, axis=0)


def period_to_reflectance(data: "np.ndarray", period: dict) -> "np.ndarray":
    """Bands → physical reflectance (0..1), regardless of the period's storage format."""
    if period["storage"] == "reflectance":
        return data
    return data / 10000


def period_to_texture_reflectance(window: "np.ndarray", period: dict) -> "np.ndarray":
    """Bands → physical reflectance for the v3 model's texture features.

    ``extract_samples_v3.py`` trained ``lulc_classifier_v3.pkl`` on 3x3
    standard deviations calculated from reflectance, not raw Sentinel-2 DN.
    Keeping this conversion explicit prevents a 10,000x feature-scale mismatch
    when inference reads a reflectance COG.
    """
    return period_to_reflectance(window, period)


def s2_assets() -> list[str]:
    """Sorted list of all S2 GeoTIFF paths (fallback source)."""
    return sorted(str(p) for p in S2_DIR.glob("*.tif")) if S2_DIR.exists() else []

def cog_available(period: dict | None = None) -> bool:
    path = (period or PERIODS[DEFAULT_PERIOD])["cog_path"]
    return RASTERIO_OK and path.exists()

_PERIOD_BOUNDS_WGS84: dict[str, tuple[float, float, float, float]] = {}


def period_bounds_wgs84(period: dict | None = None) -> list[float] | None:
    """Return a period COG's [south, west, north, east] bounds in WGS84."""
    cfg = period or PERIODS[DEFAULT_PERIOD]
    if not cog_available(cfg):
        return None
    path = str(cfg["cog_path"])
    if path not in _PERIOD_BOUNDS_WGS84:
        with rasterio.open(cfg["cog_path"]) as ds:
            from rasterio.warp import transform_bounds
            l, b, r, t = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
            _PERIOD_BOUNDS_WGS84[path] = (b, l, t, r)
    return list(_PERIOD_BOUNDS_WGS84[path])


def cog_bounds_wgs84() -> list[float] | None:
    """Backward-compatible default-period bounds used by metadata and change tiles."""
    return period_bounds_wgs84(PERIODS[DEFAULT_PERIOD])


def point_within_bounds(lat: float, lon: float, bounds: list[float]) -> bool:
    south, west, north, east = bounds
    return south <= lat <= north and west <= lon <= east


def validate_point_coverage(lat: float, lon: float, period: dict | None = None) -> None:
    """Validate a point against the same COG extent advertised by metadata."""
    bounds = period_bounds_wgs84(period)
    if bounds is not None and not point_within_bounds(lat, lon, bounds):
        raise HTTPException(
            status_code=404,
            detail="The selected point is outside the available satellite coverage",
        )


def period_data_version(period: dict) -> str:
    """Stable cache-buster that changes whenever the source COG changes."""
    try:
        stat = period["cog_path"].stat()
    except OSError:
        return "unavailable"
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def tile_cache_headers(period: dict) -> dict[str, str]:
    version = period_data_version(period)
    return {
        "Cache-Control": (
            f"public, max-age={TILE_CACHE_SECONDS}, s-maxage={TILE_CACHE_SECONDS}, "
            "stale-while-revalidate=3600"
        ),
        "ETag": f'"{version}"',
        "X-Data-Version": version,
    }


def period_evidence(period_id: str, period: dict, *, quality: dict | None = None) -> dict:
    """Public, path-free lineage and quality disclosure for an analysis result."""
    evidence_quality = {
        "grade": "limited",
        "mask_type": "nodata_only",
        "nodata_mask_applied": True,
        "cloud_mask_applied": False,
        "scl_available": False,
        "limitation": (
            "The application mosaic retains seven reflectance bands but no SCL/cloud QA band; "
            "valid-data masking is applied, while cloud and cloud-shadow screening cannot be verified."
        ),
    }
    if quality:
        evidence_quality.update(quality)
    return {
        "kind": "derived_observation",
        "source": S2_SOURCE,
        "mission": "Sentinel-2",
        "product": S2_PRODUCT,
        "period_id": period_id,
        "acquisition_window": period["date_range"],
        "processing": "Seasonal multi-scene mosaic; spectral indices derived on demand",
        "spatial_resolution_m": 10,
        "bands": S2_BANDS,
        "storage": period["storage"],
        "data_version": period_data_version(period),
        "provenance_completeness": "partial",
        "quality": evidence_quality,
    }


# ── ML land-cover classifier ─────────────────────────────────────
# v1: RandomForest (6 features: NDVI/NDRE/NDWI/NDMI/BSI/B08), trained by
#     src/processing/train_lulc_rf.py (legacy). Kept loaded and in active use
#     by /api/zone_stats's bulk per-pixel classification, which builds the
#     6-feature array — swapping the global model there would break it, so
#     v1 and v2 are deliberately kept as separate models/globals.
# v3: XGBoost (13 features: v1's 6 + 7 std_* 3x3-texture features), trained
#     by src/processing/extract_samples_v3.py + train_xgb_v3.py. Used by
#     /api/pixel and the change-analysis endpoints. The V2 suffix on the
#     globals/functions remains for API compatibility with the original model.
# Both optional: if a pickle isn't there yet, the relevant ml_* fields are
# just omitted and everything else keeps working.
CLASSIFIER  = None
CLASS_NAMES: "list[str] | None" = None
_CLASSIFIER_LOAD_ATTEMPTED = False
_CLASSIFIER_LOCK = threading.Lock()

CLASSIFIER_V2 = None
LABEL_ENCODER_V2 = None
CLASS_NAMES_V2: "list[str] | None" = None
_CLASSIFIER_V2_LOAD_ATTEMPTED = False
_CLASSIFIER_V2_LOCK = threading.Lock()

_ML_CLASS_RU = {
    "water":              "Вода",
    "dense_vegetation":   "Густая растительность",
    "agriculture":        "Сельхозугодья",
    "sparse_vegetation":  "Разреженная растительность",
    "bare_soil":          "Голая почва",
    "urban":              "Застройка",
}

def load_classifier():
    global CLASSIFIER, CLASS_NAMES, _CLASSIFIER_LOAD_ATTEMPTED
    if _CLASSIFIER_LOAD_ATTEMPTED:
        return
    with _CLASSIFIER_LOCK:
        if _CLASSIFIER_LOAD_ATTEMPTED:
            return
        _CLASSIFIER_LOAD_ATTEMPTED = True
        if not (SKLEARN_OK and LULC_MODEL_PATH.exists()):
            return
        try:
            with open(LULC_MODEL_PATH, "rb") as f:
                saved = pickle.load(f)
            CLASSIFIER  = saved["model"]
            CLASS_NAMES = list(saved["label_encoder"].classes_)
            print(f"[ok] LULC classifier loaded: {len(CLASS_NAMES)} classes")
        except Exception as e:
            print(f"[warning] failed to load LULC classifier: {e}")


def load_classifier_v2():
    global CLASSIFIER_V2, LABEL_ENCODER_V2, CLASS_NAMES_V2, _CLASSIFIER_V2_LOAD_ATTEMPTED
    if _CLASSIFIER_V2_LOAD_ATTEMPTED:
        return
    with _CLASSIFIER_V2_LOCK:
        if _CLASSIFIER_V2_LOAD_ATTEMPTED:
            return
        _CLASSIFIER_V2_LOAD_ATTEMPTED = True
        if not LULC_MODEL_V2_PATH.exists():
            return
        try:
            with open(LULC_MODEL_V2_PATH, "rb") as f:
                saved = pickle.load(f)
            CLASSIFIER_V2    = saved["model"]
            LABEL_ENCODER_V2 = saved["label_encoder"]
            CLASS_NAMES_V2   = list(saved["classes"])
            print(f"[ok] LULC classifier v3 (XGBoost) loaded: {len(CLASS_NAMES_V2)} classes")
        except Exception as e:
            print(f"[warning] failed to load LULC classifier v2: {e}")


def classify_ml(ndvi, ndre, ndwi, ndmi, bsi, b08):
    """RandomForest land-cover prediction from the 5 indices + B08. None if unavailable.
    Legacy v1 — kept for /api/zone_stats's bulk classification (6 features)."""
    load_classifier()
    if CLASSIFIER is None or None in (ndvi, ndre, ndwi, ndmi, bsi, b08):
        return None
    feats = np.array([[ndvi, ndre, ndwi, ndmi, bsi, b08]], dtype=np.float32)
    proba = CLASSIFIER.predict_proba(feats)[0]
    idx   = int(np.argmax(proba))
    cls   = CLASS_NAMES[idx]
    return {
        "class":         cls,
        "class_ru":      _ML_CLASS_RU.get(cls, cls),
        "confidence":    round(float(proba[idx]), 4),
        "probabilities": {CLASS_NAMES[i]: round(float(p), 4) for i, p in enumerate(proba)},
    }


def classify_ml_v2(ndvi, ndre, ndwi, ndmi, bsi, b08, std_bands: "dict | None"):
    """XGBoost land-cover prediction from the 6 v1 features + 7 std_* texture
    features (3x3 window std per band, physical reflectance)."""
    load_classifier_v2()
    if CLASSIFIER_V2 is None or None in (ndvi, ndre, ndwi, ndmi, bsi, b08) or not std_bands:
        return None
    feats = np.array([[
        ndvi, ndre, ndwi, ndmi, bsi, b08,
        std_bands["B02"], std_bands["B03"], std_bands["B04"],
        std_bands["B05"], std_bands["B08"], std_bands["B8A"], std_bands["B11"],
    ]], dtype=np.float32)
    proba = CLASSIFIER_V2.predict_proba(feats)[0]
    idx   = int(np.argmax(proba))
    cls   = LABEL_ENCODER_V2.inverse_transform([idx])[0]
    return {
        "class":         cls,
        "class_ru":      _ML_CLASS_RU.get(cls, cls),
        "confidence":    round(float(proba[idx]), 4),
        "probabilities": {CLASS_NAMES_V2[i]: round(float(p), 4) for i, p in enumerate(proba)},
    }


# ── Band indices (0-based) inside the 8-band S2 TIF ──────────────
# B02=0 B03=1 B04=2 B05=3 B08=4 B8A=5 B11=6 SCL=7
_B02, _B03, _B04, _B05, _B08, _B8A, _B11 = 0, 1, 2, 3, 4, 5, 6


# ── Layer config ──────────────────────────────────────────────────
# Ranges measured from the actual mosaic (random full-res sample, ~15.4M valid px),
# not textbook guesses — this region's real NDVI/NDRE etc. sit far narrower and/or
# off-centre than generic ranges assume, so a generic (-1,1)-ish stretch washes out
# to one flat hue. Bounds track the 2nd–98th percentile of real data per index —
# tighter than 1st–99th for more contrast, at the cost of hard-clipping the rarest
# extreme pixels (e.g. open water on NDWI, very dense canopy on NDVI) to the edge colour.
LAYERS = {
    "ndvi": {"label": "NDVI — растительность", "range": (0.02,  0.21), "cmap": "rdylgn"},
    "ndwi": {"label": "NDWI — водные объекты", "range": (-0.27, -0.10),"cmap": "rdbu"},
    "ndre": {"label": "NDRE — стресс растений","range": (-0.03, 0.51), "cmap": "rdylgn"},
    "ndmi": {"label": "NDMI — влажность почвы","range": (-0.20, 0.16), "cmap": "rdbu"},
    "bsi":  {"label": "BSI — голая почва",     "range": (0.12,  0.29), "cmap": "oranges"},
    "savi": {"label": "SAVI — покрытие раст.", "range": (-0.10, 0.35), "cmap": "rdylgn"},
    "nbr":  {"label": "NBR — деградация",      "range": (-0.22, 0.29), "cmap": "rdylgn"},
}

# Change-detection sign convention: for every index except BSI, a physical
# increase already reads as "improvement" (more vegetation/moisture/water).
# BSI is the one index where an increase (more bare soil) is degradation, so
# it's flipped here — this keeps "positive signed delta = green = улучшение"
# consistent for every index, both in the /tiles/change colormap and in
# /api/change_stats's "direction" field.
DIRECTION_SIGN = {
    "ndvi": 1, "ndre": 1, "ndwi": 1, "ndmi": 1, "savi": 1, "nbr": 1,
    "bsi": -1,
}

CMAP_CSS = {
    "ndvi": "linear-gradient(to right,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)",
    "ndwi": "linear-gradient(to right,#b2182b,#f7f7f7,#2166ac)",   # rdbu: dry→water
    "ndre": "linear-gradient(to right,#d73027,#fee08b,#1a9850)",   # rdylgn: stress→healthy
    "ndmi": "linear-gradient(to right,#67001f,#f4a582,#f7f7f7,#92c5de,#053061)",
    "bsi":  "linear-gradient(to right,#fff5eb,#fdd0a2,#fd8d3c,#d94801,#7f2704)",
    "savi": "linear-gradient(to right,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)",
    "nbr":  "linear-gradient(to right,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)",
}

# 6-stop discrete colormaps (index → RGB)
_RAW_CMAPS = {
    "rdylgn": [(0,(165,0,38)),(51,(215,48,39)),(102,(253,174,97)),
               (153,(166,217,106)),(204,(26,152,80)),(255,(0,104,55))],
    "blues":  [(0,(247,251,255)),(51,(198,219,239)),(102,(107,174,214)),
               (153,(33,113,181)),(204,(8,81,156)),(255,(8,48,107))],
    "greens": [(0,(247,252,245)),(51,(199,233,192)),(102,(116,196,118)),
               (153,(35,139,69)),(204,(0,109,44)),(255,(0,68,27))],
    "rdbu":   [(0,(103,0,31)),(51,(214,96,77)),(102,(253,219,199)),
               (153,(146,197,222)),(204,(33,102,172)),(255,(5,48,97))],
    "oranges":[(0,(255,245,235)),(51,(253,208,162)),(102,(253,141,60)),
               (153,(217,71,1)),(204,(166,54,3)),(255,(127,39,4))],
    # Diverging red→white→green for change-detection deltas (0 = no change,
    # rescaled to sit exactly at index 128 by the caller).
    "red_white_green": [(0,(103,0,31)),(64,(214,96,77)),(128,(255,255,255)),
                         (192,(102,189,99)),(255,(0,104,55))],
}

def _make_lut(name: str) -> "np.ndarray":
    stops = _RAW_CMAPS[name]
    lut   = np.zeros((256, 3), dtype=np.uint8)
    for i in range(len(stops) - 1):
        i0, c0 = stops[i];  i1, c1 = stops[i + 1]
        for v in range(i0, i1 + 1):
            t = (v - i0) / (i1 - i0 or 1)
            lut[v] = [int(c0[k] + t * (c1[k] - c0[k])) for k in range(3)]
    lut[stops[-1][0]:] = stops[-1][1]
    return lut

_LUT_CACHE: dict = {}

def get_lut(name: str) -> "np.ndarray":
    if name not in _LUT_CACHE:
        _LUT_CACHE[name] = _make_lut(name)
    return _LUT_CACHE[name]


# ── Transparent PNG ───────────────────────────────────────────────
_TRANS: bytes | None = None

def blank_tile() -> bytes:
    global _TRANS
    if _TRANS is None:
        buf = io.BytesIO()
        Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(buf, "PNG")
        _TRANS = buf.getvalue()
    return _TRANS


# ── Mosaic reader (manual, rio-tiler version-agnostic) ────────────
def mosaic_tile(x: int, y: int, z: int, period: dict):
    """
    Read one 256×256 tile, preferring the merged COG mosaic.
    Returns (data: float32 array (bands, 256, 256), mask: uint8 (256, 256))
    or (None, None) when no coverage.

    Mask is derived from the raw bands ourselves — valid unless ALL bands are
    the period's nodata value — rather than trusting rio-tiler's img.mask.
    rio-tiler flags a pixel nodata as soon as ANY single band matches nodata,
    which is too aggressive here: it kills real pixels where one band
    legitimately reads ~0 (and the computed index is ≈0) while the rest of the
    bands, and the COG's actual nodata convention, say the pixel is valid.
    """
    if cog_available(period):
        try:
            with Reader(str(period["cog_path"])) as src:
                img = src.tile(x, y, z, tilesize=256)
            data = img.data.astype(np.float32)
            mask = (~period_nodata_mask(data, period)).astype(np.uint8) * 255
            return data, mask
        except TileOutsideBounds:
            return None, None
        except Exception as e:
            print(f"  COG tile read failed, falling back to per-scene mosaic: {e}")

    return _mosaic_tile_legacy(x, y, z)


def _mosaic_tile_legacy(x: int, y: int, z: int):
    """Fallback: merge every per-scene S2 TIF on the fly (used if COG is unavailable)."""
    assets = s2_assets()
    if not assets:
        return None, None

    result_data: "np.ndarray | None" = None
    result_mask: "np.ndarray | None" = None

    for asset in assets:
        try:
            with Reader(asset) as src:
                img = src.tile(x, y, z, tilesize=256)
        except TileOutsideBounds:
            continue
        except Exception as e:
            print(f"  skip {Path(asset).name}: {e}")
            continue

        d = img.data.astype(np.float32)                       # (bands, 256, 256)
        m = (~np.all(d == 0, axis=0)).astype(np.uint8) * 255  # valid unless ALL bands == 0

        if result_data is None:
            result_data = d.copy()
            result_mask = m.copy()
        else:
            # Where current result is nodata (mask==0), fill from this tile
            need = result_mask == 0
            if not need.any():
                break                      # fully filled → stop early
            result_data[:, need] = d[:, need]
            result_mask[need]    = m[need]

    return result_data, result_mask


# ── Index computation ─────────────────────────────────────────────
def compute_index(data: "np.ndarray", layer: str, period: dict) -> "np.ndarray":
    """
    data: float32 (bands, 256, 256) — period's native storage format (DN or reflectance)
    Returns float32 (256, 256) index values.
    """
    eps = 1e-10
    refl = period_to_reflectance(data, period)
    b02 = refl[_B02]
    b03 = refl[_B03]
    b04 = refl[_B04]
    b05 = refl[_B05]
    b08 = refl[_B08]
    b8a = refl[_B8A]
    b11 = refl[_B11]

    if   layer == "ndvi": return (b08 - b04) / (b08 + b04 + eps)
    elif layer == "ndwi": return (b03 - b08) / (b03 + b08 + eps)
    elif layer == "ndre": return (b08 - b05) / (b08 + b05 + eps)
    elif layer == "ndmi": return (b8a - b11) / (b8a + b11 + eps)
    elif layer == "bsi":
        num = (b11 + b04) - (b08 + b02)
        den = (b11 + b04) + (b08 + b02)
        return num / (den + eps)
    elif layer == "savi": return (b08 - b04) / (b08 + b04 + 0.5 + eps) * 1.5
    elif layer == "nbr":  return (b08 - b11) / (b08 + b11 + eps)
    return np.zeros(data.shape[1:], dtype=np.float32)


# ── Experimental linear-trend forecast helpers ───────────────────
_INDEX_LIMITS = {
    "ndvi": (-1.0, 1.0), "ndwi": (-1.0, 1.0), "ndre": (-1.0, 1.0),
    "ndmi": (-1.0, 1.0), "bsi": (-1.0, 1.0), "nbr": (-1.0, 1.0),
    "savi": (-1.5, 1.5),
}


def _period_year(period_id: str) -> int | None:
    try:
        year = int(period_id[:4])
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2200 else None


def forecast_source_periods() -> list[tuple[int, str, dict]]:
    """Available annual periods sorted by year.

    At least three observations are required. The registry remains the single
    source of truth, so adding a future annual mosaic automatically extends the
    baseline without changing the forecast implementation.
    """
    result = []
    for period_id, cfg in PERIODS.items():
        year = _period_year(period_id)
        if year is not None and cog_available(cfg):
            result.append((year, period_id, cfg))
    return sorted(result, key=lambda item: item[0])


def forecast_config() -> dict:
    sources = forecast_source_periods()
    years = [year for year, _, _ in sources]
    enabled = len(sources) >= 3
    latest_year = years[-1] if years else None
    return {
        "enabled": enabled,
        "method": "ordinary_least_squares",
        "prototype": True,
        "source_years": years,
        "observations": len(years),
        "min_target_year": latest_year + 1 if enabled else None,
        "max_target_year": latest_year + MAX_FORECAST_HORIZON_YEARS if enabled else None,
        "confidence": "low",
    }


def validate_forecast_request(index: str, target_year: int) -> list[tuple[int, str, dict]]:
    if index not in LAYERS:
        raise HTTPException(status_code=404, detail=f"Неизвестный индекс: {index}")
    sources = forecast_source_periods()
    if len(sources) < 3:
        raise HTTPException(
            status_code=503,
            detail="Для линейного прогноза нужны минимум три доступных годовых периода",
        )
    latest_year = sources[-1][0]
    if target_year <= latest_year or target_year > latest_year + MAX_FORECAST_HORIZON_YEARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Год прогноза должен быть от {latest_year + 1} "
                f"до {latest_year + MAX_FORECAST_HORIZON_YEARS}"
            ),
        )
    return sources


def linear_trend_array(years: "np.ndarray", values: "np.ndarray", target_year: int) -> "np.ndarray":
    """OLS forecast along axis 0 for scalar, point, or raster values."""
    x = np.asarray(years, dtype=np.float32)
    y = np.asarray(values, dtype=np.float32)
    if x.ndim != 1 or y.shape[0] != x.size or x.size < 3:
        raise ValueError("linear trend requires at least three matching observations")
    centered = x - x.mean()
    denominator = float(np.sum(centered ** 2))
    if denominator <= 0:
        raise ValueError("forecast years must contain temporal variation")
    reshape = (x.size,) + (1,) * (y.ndim - 1)
    slope = np.sum(centered.reshape(reshape) * y, axis=0) / denominator
    return y.mean(axis=0) + slope * (float(target_year) - float(x.mean()))


def linear_trend_summary(years: list[int], values: list[float], target_year: int, index: str) -> dict:
    """Point estimate plus a transparent sensitivity envelope.

    The range is not called a confidence interval: with only three annual
    observations that would imply unjustified certainty. Instead it compares
    the all-years OLS projection with projections from each adjacent observed
    annual slope and expands the envelope by the in-sample fit RMSE.
    """
    x = np.asarray(years, dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    predicted_raw = float(linear_trend_array(x, y, target_year))
    centered = x - x.mean()
    slope = float(np.sum(centered * y) / np.sum(centered ** 2))
    fitted = y.mean() + slope * centered
    residuals = y - fitted
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    ss_total = float(np.sum((y - y.mean()) ** 2))
    ss_residual = float(np.sum(residuals ** 2))
    r_squared = 1.0 - ss_residual / ss_total if ss_total > 1e-12 else 1.0

    horizon = float(target_year - years[-1])
    adjacent_slopes = np.diff(y) / np.diff(x)
    alternatives = [predicted_raw]
    alternatives.extend(float(y[-1] + annual_slope * horizon) for annual_slope in adjacent_slopes)
    lower_raw = min(alternatives) - rmse
    upper_raw = max(alternatives) + rmse

    lower_limit, upper_limit = _INDEX_LIMITS[index]
    predicted = float(np.clip(predicted_raw, lower_limit, upper_limit))
    lower = float(np.clip(lower_raw, lower_limit, upper_limit))
    upper = float(np.clip(upper_raw, lower_limit, upper_limit))
    latest_value = float(y[-1])
    delta = predicted - latest_value
    signed_annual_change = slope * DIRECTION_SIGN.get(index, 1)
    if abs(signed_annual_change) < 0.005:
        direction, direction_ru = "stable", "без выраженного тренда"
    elif signed_annual_change > 0:
        direction, direction_ru = "improving", "улучшение"
    else:
        direction, direction_ru = "degrading", "ухудшение"

    same_sign = bool(np.all(adjacent_slopes >= 0) or np.all(adjacent_slopes <= 0))
    trend_quality = "consistent" if same_sign and r_squared >= 0.8 else "variable"
    return {
        "predicted": round(predicted, 4),
        "latest_value": round(latest_value, 4),
        "change_from_latest": round(delta, 4),
        "slope_per_year": round(slope, 4),
        "sensitivity_low": round(min(lower, upper), 4),
        "sensitivity_high": round(max(lower, upper), 4),
        "fit_rmse": round(rmse, 4),
        "r_squared": round(float(np.clip(r_squared, 0.0, 1.0)), 4),
        "direction": direction,
        "direction_ru": direction_ru,
        "trend_quality": trend_quality,
    }


def read_point_indices(lat: float, lon: float, period: dict) -> dict[str, float] | None:
    if not (RASTERIO_OK and cog_available(period)):
        return None
    with rasterio.open(period["cog_path"]) as src:
        transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        px, py = transformer.transform(lon, lat)
        row, col = src.index(px, py)
        if not (0 <= row < src.height and 0 <= col < src.width):
            return None
        raw = src.read(window=((row, row + 1), (col, col + 1))).astype(np.float32)
        if raw.shape[0] < 7 or period_nodata_mask(raw[:7], period).all():
            return None
        return {
            index: float(compute_index(raw[:7], index, period)[0, 0])
            for index in LAYERS
        }


# ── PNG renderers ─────────────────────────────────────────────────
def render_index(index: "np.ndarray", mask: "np.ndarray",
                 cmap: str, vmin: float, vmax: float) -> bytes:
    clipped  = np.clip(index, vmin, vmax)
    scaled   = ((clipped - vmin) / (vmax - vmin) * 255).astype(np.uint8)
    lut      = get_lut(cmap)               # (256, 3)
    rgb      = lut[scaled]                 # (256, 256, 3)
    rgba     = np.concatenate([rgb, mask[:, :, None]], axis=-1)
    buf      = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


def render_true_color(data: "np.ndarray", mask: "np.ndarray", period: dict) -> bytes:
    """Render B04/B03/B02 BOA reflectance with a stable true-colour stretch."""
    reflectance = period_to_reflectance(data, period)
    rgb = np.stack((reflectance[_B04], reflectance[_B03], reflectance[_B02]), axis=-1)
    # The 2.5x Copernicus true-colour convention maps 0.4 reflectance to white.
    rgb = np.clip(rgb * 2.5, 0.0, 1.0)
    rgb = np.power(rgb, 1 / 2.2)
    rgb_u8 = np.rint(rgb * 255).astype(np.uint8)
    rgba = np.concatenate([rgb_u8, mask[:, :, None]], axis=-1)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


# ════════════════════════════════════════════════════════════════
#   APP
# ════════════════════════════════════════════════════════════════

app = FastAPI(
    title      = "GeoAI-TKO API",
    version    = "4.0.0",
    docs_url   = "/api/docs",
    redoc_url  = "/api/redoc",
)

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]
for production_origin in ("https://geo-tko.online", "https://www.geo-tko.online"):
    if production_origin not in CORS_ORIGINS:
        CORS_ORIGINS.append(production_origin)


_EXPENSIVE_API_PATHS = {
    "/api/analyze",
    "/api/change_stats",
    "/api/forecast/point",
    "/api/transect",
    "/api/zone_report",
    "/api/zone_stats",
    "/api/zone_timeseries",
    "/api/timelapse/scenes",
}


def _rate_limit_policy(path: str) -> tuple[str, int] | None:
    if path.startswith("/tiles/"):
        return "tiles", TILE_RATE_LIMIT
    if path in _EXPENSIVE_API_PATHS:
        return "analysis", ANALYSIS_RATE_LIMIT
    if path.startswith("/api/"):
        return "api", API_RATE_LIMIT
    return None


@app.middleware("http")
async def rate_limit_requests(request: Request, call_next):
    if not RATE_LIMIT_ENABLED or request.method == "OPTIONS":
        return await call_next(request)

    policy = _rate_limit_policy(request.url.path)
    if policy is None:
        return await call_next(request)

    bucket, limit = policy
    client = request.client.host if request.client else "unknown"
    allowed, remaining, retry_after = _API_RATE_LIMITER.consume(
        f"{bucket}:{client}", limit=limit, window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    )
    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
    }
    if not allowed:
        headers["Retry-After"] = str(retry_after)
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please retry later."},
            headers=headers,
        )

    response = await call_next(request)
    response.headers.update(headers)
    return response


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    request_id = uuid.uuid4().hex
    started = time.perf_counter()
    should_log = not request.url.path.startswith(("/tiles/", "/data/"))
    try:
        response = await call_next(request)
    except Exception as exc:
        if should_log:
            log_event(
                logging.ERROR,
                "http_request_failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error=type(exc).__name__,
            )
        raise
    response.headers["X-Request-ID"] = request_id
    if should_log:
        log_event(
            logging.INFO if response.status_code < 500 else logging.ERROR,
            "http_request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            client=request.client.host if request.client else None,
        )
    return response


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"}:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    return JSONResponse(status_code=413, content={"detail": "Тело запроса слишком большое"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Некорректный Content-Length"})
    return await call_next(request)


def health_payload() -> dict:
    assets = s2_assets()
    forecast = forecast_config()
    account_database = ACCOUNT_STORE.health_status()
    account_backup = backup_status(ACCOUNT_BACKUP_DIR, BACKUP_MAX_AGE_HOURS)
    account_backup["required"] = BACKUP_HEALTH_REQUIRED
    account_mail = ACCOUNT_MAILER.status()
    operationally_ready = (
        account_database["ok"]
        and account_mail["configured"]
        and (not BACKUP_HEALTH_REQUIRED or account_backup["fresh"])
    )
    return {
        "status":    "ok" if operationally_ready else "degraded",
        "version":   "4.0.0",
        "tiler":     TILER_OK,
        "rasterio":  RASTERIO_OK,
        "ai":        AI_OK,
        "cog":       cog_available(),
        "s2_tiles":  len(assets),
        "ai_ready":  bool(os.getenv("GROQ_API_KEY") or os.getenv("DEEPSEEK_API_KEY")),
        "lulc_classifier": bool(SKLEARN_OK and LULC_MODEL_PATH.exists()),
        "lulc_classifier_v2": bool(SKLEARN_OK and LULC_MODEL_V2_PATH.exists()),
        "lulc_classifier_loaded": CLASSIFIER is not None,
        "lulc_classifier_v2_loaded": CLASSIFIER_V2 is not None,
        "linear_forecast": forecast["enabled"],
        "linear_forecast_source_years": forecast["source_years"],
        "scene_catalogue": CDSE_SCENE_CATALOG.capabilities(),
        "monitoring": monitoring_service_status(),
        "accounts": {
            "database": account_database,
            "email": account_mail,
            "backup": account_backup,
        },
    }


@app.get("/healthz")
async def healthz():
    """Minimal liveness probe. It intentionally performs no dependency checks."""
    return {"status": "ok", "version": "4.0.0"}


@app.get("/readyz")
async def readyz():
    """Readiness probe for serving core raster and account requests."""
    account_database = ACCOUNT_STORE.health_status()
    ready = bool(TILER_OK and RASTERIO_OK and cog_available() and account_database["ok"])
    content = {
        "status": "ready" if ready else "not_ready",
        "components": {
            "tiles": bool(TILER_OK and RASTERIO_OK and cog_available()),
            "accounts": bool(account_database["ok"]),
        },
    }
    return JSONResponse(status_code=200 if ready else 503, content=content)


@app.get("/health")
async def health():
    """Frontend-safe component health without host filesystem disclosure."""
    return health_payload()


@app.get("/api/periods")
async def periods():
    return [
        {
            "period_id": k,
            "label": v["label"],
            "date_range": v["date_range"],
            "available": cog_available(v),
            "data_version": period_data_version(v),
            "evidence": period_evidence(k, v),
        }
        for k, v in PERIODS.items()
    ]


class TimelapseSceneSearchReq(BaseModel):
    bbox: list[float] = Field(min_length=4, max_length=4)
    start_date: date
    end_date: date
    max_cloud_cover: float = Field(default=30.0, ge=0.0, le=100.0)
    limit: int = Field(default=30, ge=1, le=100)


def validate_scene_search(req: TimelapseSceneSearchReq) -> list[float]:
    west, south, east, north = (float(value) for value in req.bbox)
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise HTTPException(status_code=400, detail="AOI bounds must contain finite coordinates")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(status_code=400, detail="AOI bounds are invalid or reversed")
    if (east - west) * (north - south) > 100:
        raise HTTPException(status_code=400, detail="AOI is too large for interactive scene search")
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail="End date must not be before start date")
    if (req.end_date - req.start_date).days > 3660:
        raise HTTPException(status_code=400, detail="Scene-search date range cannot exceed ten years")
    return [west, south, east, north]


@app.get("/api/timelapse/capabilities")
async def timelapse_capabilities():
    return CDSE_SCENE_CATALOG.capabilities()


@app.post("/api/timelapse/scenes")
async def timelapse_scenes(req: TimelapseSceneSearchReq):
    if not CDSE_CATALOG_ENABLED:
        raise HTTPException(status_code=503, detail="CDSE scene catalogue is disabled")
    bbox = validate_scene_search(req)
    if not _CATALOG_SEMAPHORE.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Scene catalogue is busy; retry shortly")
    try:
        try:
            result = await run_in_threadpool(
                CDSE_SCENE_CATALOG.search,
                bbox=bbox,
                start_date=req.start_date.isoformat(),
                end_date=req.end_date.isoformat(),
                max_cloud_cover=req.max_cloud_cover,
                limit=req.limit,
            )
        except SceneSearchError as exc:
            log_event(logging.WARNING, "cdse_scene_search_failed", error=type(exc).__name__)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        _CATALOG_SEMAPHORE.release()

    return {
        **result,
        "capabilities": CDSE_SCENE_CATALOG.capabilities(),
        "query": {
            "bbox": bbox,
            "start_date": req.start_date.isoformat(),
            "end_date": req.end_date.isoformat(),
            "max_cloud_cover": req.max_cloud_cover,
            "limit": req.limit,
        },
        "disclaimer": "Cloud cover is product metadata for the source tile, not an AOI-specific cloud measurement.",
    }


# ════════════════════════════════════════════════════════════════
#   TILE ENDPOINT
# ════════════════════════════════════════════════════════════════

@app.get("/tiles/{layer}/{z}/{x}/{y}.png")
def tile(layer: str, z: int, x: int, y: int, period: str = Query(DEFAULT_PERIOD)):
    if layer != "rgb" and layer not in LAYERS:
        raise HTTPException(status_code=404, detail=f"Неизвестный слой: {layer}")
    if not TILER_OK:
        raise HTTPException(status_code=503, detail="Сервис тайлов недоступен")

    period_cfg = resolve_period(period)
    cache_headers = tile_cache_headers(period_cfg)
    data, mask = mosaic_tile(x, y, z, period_cfg)

    if data is None:
        return Response(content=blank_tile(), media_type="image/png",
                        headers=cache_headers)

    # Normalize mask to uint8 0/255. rio-tiler 9.x returns a uint16 mask
    # (valid = 65535); stacking that with uint8 RGB would upcast the RGBA
    # array to uint16 and make PIL's "RGBA" fromarray throw → blank tiles.
    mask = (mask > 0).astype(np.uint8) * 255

    try:
        if layer == "rgb":
            content = render_true_color(data, mask, period_cfg)
        else:
            cfg = LAYERS[layer]
            vmin, vmax = cfg["range"]
            index = compute_index(data, layer, period_cfg)
            content = render_index(index, mask, cfg["cmap"], vmin, vmax)
    except Exception as e:
        log_event(
            logging.ERROR, "tile_render_failed", layer=layer, z=z, x=x, y=y,
            period=period, error=type(e).__name__,
        )
        return Response(content=blank_tile(), media_type="image/png", headers=cache_headers)

    return Response(content=content, media_type="image/png",
                    headers=cache_headers)


@app.get("/tiles/forecast/{index}/{target_year}/{z}/{x}/{y}.png")
async def forecast_tile(index: str, target_year: int, z: int, x: int, y: int):
    """Render the all-years OLS extrapolation for one spectral index.

    This deliberately remains an on-demand prototype. A later ML pipeline can
    replace the source with precomputed forecast COGs while keeping the same
    frontend tile contract.
    """
    if not TILER_OK:
        raise HTTPException(status_code=503, detail="Сервис тайлов недоступен")
    sources = validate_forecast_request(index, target_year)
    reads = await asyncio.gather(*(
        run_in_threadpool(mosaic_tile, x, y, z, cfg)
        for _, _, cfg in sources
    ))
    if any(data is None for data, _ in reads):
        return Response(
            content=blank_tile(), media_type="image/png",
            headers={"Cache-Control": "public, max-age=60"},
        )

    valid = np.logical_and.reduce([(mask > 0) for _, mask in reads])
    if not valid.any():
        return Response(
            content=blank_tile(), media_type="image/png",
            headers={"Cache-Control": "public, max-age=60"},
        )

    try:
        years = np.asarray([year for year, _, _ in sources], dtype=np.float32)
        values = np.stack([
            compute_index(data, index, cfg)
            for (data, _), (_, _, cfg) in zip(reads, sources)
        ])
        predicted = linear_trend_array(years, values, target_year)
        lower_limit, upper_limit = _INDEX_LIMITS[index]
        predicted = np.clip(predicted, lower_limit, upper_limit)
        cfg = LAYERS[index]
        vmin, vmax = cfg["range"]
        content = render_index(
            predicted,
            valid.astype(np.uint8) * 255,
            cfg["cmap"], vmin, vmax,
        )
    except Exception as exc:
        print(f"forecast render error {index}/{target_year}/{z}/{x}/{y}: {exc}")
        return Response(content=blank_tile(), media_type="image/png")

    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Forecast-Method": "linear-trend-prototype",
        },
    )


@app.get("/api/forecast/point")
@limit_analysis
def forecast_point(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    index: str = Query("ndvi"),
    target_year: int = Query(...),
):
    sources = validate_forecast_request(index, target_year)
    validate_point_coverage(lat, lon, sources[-1][2])
    history = []
    observed_values = []
    for year, period_id, cfg in sources:
        values = read_point_indices(lat, lon, cfg)
        if values is None:
            raise HTTPException(
                status_code=404,
                detail=f"Нет данных для точки в периоде {period_id}",
            )
        value = values[index]
        if not math.isfinite(value):
            raise HTTPException(status_code=404, detail=f"Некорректное значение в периоде {period_id}")
        observed_values.append(float(value))
        history.append({
            "year": year,
            "period": period_id,
            "label": cfg["label"],
            "value": round(float(value), 4),
        })

    trend = linear_trend_summary(
        [item["year"] for item in history],
        observed_values,
        target_year,
        index,
    )
    return {
        "lat": lat,
        "lon": lon,
        "index": index,
        "index_label": LAYERS[index]["label"],
        "target_year": target_year,
        "method": "ordinary_least_squares",
        "prototype": True,
        "confidence": "low",
        "history": history,
        "trend": trend,
        "evidence": {
            "kind": "modeled_scenario",
            "source": S2_SOURCE,
            "product": S2_PRODUCT,
            "acquisition_window": f"{sources[0][0]}–{sources[-1][0]}",
            "processing": "Ordinary least-squares extrapolation from available annual observations",
            "data_version": ".".join(period_data_version(cfg) for _, _, cfg in sources),
            "provenance_completeness": "partial",
            "quality": {
                "grade": "experimental",
                "confidence": "low",
                "mask_type": "nodata_only",
                "cloud_mask_applied": False,
            },
        },
        "disclaimer": (
            "Экспериментальная экстраполяция трёх летних наблюдений. "
            "Она показывает сценарий продолжения текущего тренда, а не гарантированный прогноз."
        ),
    }


_CHANGE_RESCALE_CACHE: dict = {}   # (period_before, period_after, index) -> max_abs (float)
_CHANGE_RESCALE_Z = 6               # coarse overview zoom — whole AOI in a handful of tiles

# The mosaics are built per-MGRS-tile (~110km granules), each composited from
# whatever cloud-free date was available within the season — and that date
# differs granule to granule (see D:\data\mosaics\*\compositing_stats.json).
# So a real, non-negligible chunk of "change" between two seasonal mosaics is
# just within-season phenology drift from comparing different acquisition
# dates at each granule boundary, not real land-cover change. Rather than
# smoothing that away, /tiles/change/ only renders pixels whose |signed
# delta| clears a high per-index threshold — everything else is fully
# transparent (alpha=0, not white) so the base map shows through instead of
# a washed-out haze. /api/change_stats' numeric output is unaffected.
CHANGE_THRESHOLDS = {
    "ndvi": 0.12, "ndre": 0.12, "savi": 0.12, "nbr": 0.12,
    "ndwi": 0.10, "ndmi": 0.10,
    "bsi":  0.08,
}


def _lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _compute_global_change_rescale(before_cfg: dict, after_cfg: dict, index: str) -> float:
    """One global |delta| p98 for this (periods, index) triple, computed once
    from a coarse z=6 overview of the whole AOI (a handful of tiles) instead
    of per-tile — per-tile percentiles made neighbouring tiles pick different
    color scales, producing visible square seams at tile boundaries."""
    bounds = cog_bounds_wgs84()   # [S, W, N, E]
    if bounds is None:
        return 0.1
    south, west, north, east = bounds
    x0, y0 = _lonlat_to_tile(west, north, _CHANGE_RESCALE_Z)
    x1, y1 = _lonlat_to_tile(east, south, _CHANGE_RESCALE_Z)
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)

    abs_deltas = []
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            data_before, mask_before = mosaic_tile(tx, ty, _CHANGE_RESCALE_Z, before_cfg)
            data_after,  mask_after  = mosaic_tile(tx, ty, _CHANGE_RESCALE_Z, after_cfg)
            if data_before is None or data_after is None:
                continue
            valid = (mask_before > 0) & (mask_after > 0)
            if not valid.any():
                continue
            idx_before = compute_index(data_before, index, before_cfg)
            idx_after  = compute_index(data_after, index, after_cfg)
            delta = (idx_after - idx_before) * DIRECTION_SIGN.get(index, 1)
            abs_deltas.append(np.abs(delta[valid]))

    if not abs_deltas:
        return 0.1
    return max(float(np.percentile(np.concatenate(abs_deltas), 98)), 1e-6)


@app.get("/tiles/change/{index}/{z}/{x}/{y}.png")
async def change_tile(
    index: str, z: int, x: int, y: int,
    period_before: str = Query("2023_summer"),
    period_after:  str = Query("2025_summer"),
):
    """Change-detection tile: delta = index(period_after) - index(period_before),
    signed so positive always renders green (улучшение) via DIRECTION_SIGN.
    Rescale is one global value per (periods, index) triple — computed once
    from a coarse overview and cached — so every tile shares the same color
    scale and tile-boundary seams don't appear."""
    if index not in LAYERS:
        raise HTTPException(status_code=404, detail=f"Неизвестный индекс: {index}")
    if not TILER_OK:
        raise HTTPException(status_code=503, detail="Сервис тайлов недоступен")

    before_cfg = resolve_period(period_before)
    after_cfg  = resolve_period(period_after)

    (data_before, mask_before), (data_after, mask_after) = await asyncio.gather(
        run_in_threadpool(mosaic_tile, x, y, z, before_cfg),
        run_in_threadpool(mosaic_tile, x, y, z, after_cfg),
    )

    if data_before is None or data_after is None:
        return Response(content=blank_tile(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=60"})

    valid = (mask_before > 0) & (mask_after > 0)
    if not valid.any():
        return Response(content=blank_tile(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=60"})

    try:
        idx_before = compute_index(data_before, index, before_cfg)
        idx_after  = compute_index(data_after, index, after_cfg)
        signed_delta = (idx_after - idx_before) * DIRECTION_SIGN.get(index, 1)

        # Only render pixels with a clearly significant change — sub-threshold
        # pixels go fully transparent (alpha=0) rather than white, so the base
        # map shows through instead of a washed-out white haze.
        thresh = CHANGE_THRESHOLDS.get(index, 0.05)
        valid_change_mask = np.abs(signed_delta) >= thresh

        cache_key = (period_before, period_after, index)
        if cache_key not in _CHANGE_RESCALE_CACHE:
            _CHANGE_RESCALE_CACHE[cache_key] = await run_in_threadpool(
                _compute_global_change_rescale, before_cfg, after_cfg, index)
        max_abs = _CHANGE_RESCALE_CACHE[cache_key]

        mask_u8 = (valid & valid_change_mask).astype(np.uint8) * 255
        content = render_index(signed_delta, mask_u8, "red_white_green", -max_abs, max_abs)
    except Exception as e:
        print(f"change render error {index}/{z}/{x}/{y}: {e}")
        return Response(content=blank_tile(), media_type="image/png")

    return Response(content=content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


# ════════════════════════════════════════════════════════════════
#   PIXEL VALUES
# ════════════════════════════════════════════════════════════════

def _demo(lat, lon):
    import math
    s = abs(math.sin(lat * 100) * math.cos(lon * 100))
    b = {f"B{n}": round(0.05 + s * 0.3 + i * 0.02, 3)
         for i, n in enumerate(["02","03","04","05","08","8A","11"])}
    eps = 1e-10
    b02,b03,b04 = b["B02"],b["B03"],b["B04"]
    b05,b08,b8a,b11 = b["B05"],b["B08"],b["B8A"],b["B11"]
    return {
        "ndvi": round((b08-b04)/(b08+b04+eps),4),
        "ndwi": round((b03-b08)/(b03+b08+eps),4),
        "ndre": round((b08-b05)/(b08+b05+eps),4),
        "ndmi": round((b8a-b11)/(b8a+b11+eps),4),
        "bsi":  round(((b11+b04)-(b08+b02))/((b11+b04)+(b08+b02)+eps),4),
        "savi": round((b08-b04)/(b08+b04+0.5+eps)*1.5,4),
        "nbr":  round((b08-b11)/(b08+b11+eps),4),
        "bands": b, "demo": True,
    }


@app.get("/api/pixel")
def pixel(
    # Bounds cover the full COG extent (≈40.7–46.5°N, 65.7–71.4°E) with margin.
    # The old 40.8–44.0 / 67.5–71.5 box wrongly 422'd valid points like 44.15°N.
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    period: str = Query(DEFAULT_PERIOD),
):
    import math
    result = {}
    period_cfg = resolve_period(period)
    validate_point_coverage(lat, lon, period_cfg)

    def _pixel_from(path: str):
        with rasterio.open(path) as src:
            t = Transformer.from_crs("EPSG:4326", src.crs.to_epsg(), always_xy=True)
            px, py = t.transform(lon, lat)
            row, col = src.index(px, py)
            if not (0 <= row < src.height and 0 <= col < src.width):
                return None
            raw = src.read(window=((row, row + 1), (col, col + 1))).astype(float).flatten()
            if len(raw) < 7 or period_nodata_mask(raw[:7].reshape(7, 1, 1), period_cfg).all():
                return None
            b   = period_to_reflectance(raw[:7], period_cfg)
            eps = 1e-10

            # 3x3 window (clipped at raster edges) for the v3 classifier's
            # texture features. The v3 training extractor computed these
            # standard deviations from physical reflectance.
            row_off, col_off = max(0, row - 1), max(0, col - 1)
            row_end, col_end = min(src.height, row + 2), min(src.width, col + 2)
            win = src.read(window=((row_off, row_end), (col_off, col_end))).astype(np.float32)
            win_nodata = period_nodata_mask(win, period_cfg)
            valid_px = ~win_nodata
            win_refl = period_to_texture_reflectance(win, period_cfg)
            if valid_px.sum() < 3:
                std_vals = np.zeros(7, dtype=np.float32)
            else:
                std_vals = win_refl[:, valid_px].std(axis=1)
            std_bands = {
                name: round(float(v), 4)
                for name, v in zip(("B02", "B03", "B04", "B05", "B08", "B8A", "B11"), std_vals)
            }

            return {
                "ndvi": round((b[4]-b[2])/(b[4]+b[2]+eps),4),
                "ndwi": round((b[1]-b[4])/(b[1]+b[4]+eps),4),
                "ndre": round((b[4]-b[3])/(b[4]+b[3]+eps),4),
                "ndmi": round((b[5]-b[6])/(b[5]+b[6]+eps),4),
                "bsi":  round(((b[6]+b[2])-(b[4]+b[0]))/((b[6]+b[2])+(b[4]+b[0])+eps),4),
                "savi": round((b[4]-b[2])/(b[4]+b[2]+0.5+eps)*1.5,4),
                "nbr":  round((b[4]-b[6])/(b[4]+b[6]+eps),4),
                "bands": {
                    "B02":round(b[0],4),"B03":round(b[1],4),"B04":round(b[2],4),
                    "B05":round(b[3],4),"B08":round(b[4],4),"B8A":round(b[5],4),
                    "B11":round(b[6],4),
                },
                "std_bands": std_bands,
                "demo": False,
            }

    if RASTERIO_OK and cog_available(period_cfg):
        try:
            result = _pixel_from(str(period_cfg["cog_path"])) or {}
        except Exception as e:
            print(f"COG pixel read failed: {e}")

    if not result and RASTERIO_OK:
        for asset in s2_assets():
            try:
                result = _pixel_from(asset) or {}
                if result:
                    break
            except Exception:
                continue

    if not result and ENABLE_DEMO_DATA:
        result = _demo(lat, lon)

    if not result:
        if not RASTERIO_OK or not cog_available(period_cfg):
            raise HTTPException(status_code=503, detail="Растровые данные выбранного периода недоступны")
        raise HTTPException(status_code=404, detail="Нет спутниковых данных для выбранной точки и периода")

    def safe(v):
        return None if (v is None or (isinstance(v,float) and math.isnan(v))) else v

    ndvi, ndre, ndwi, ndmi, bsi, savi, nbr = (
        safe(result.get(k)) for k in ("ndvi","ndre","ndwi","ndmi","bsi","savi","nbr")
    )
    b08 = (result.get("bands") or {}).get("B08")
    ml = classify_ml_v2(ndvi, ndre, ndwi, ndmi, bsi, b08, result.get("std_bands"))
    if ml is None:
        ml = classify_ml(ndvi, ndre, ndwi, ndmi, bsi, b08)  # fall back to v1 if v2 unavailable

    return {
        "lat": lat, "lon": lon,
        "ndvi": ndvi, "ndwi": ndwi, "ndre": ndre, "ndmi": ndmi, "bsi": bsi, "savi": savi, "nbr": nbr,
        "bands":      result.get("bands", {}),
        "ml_class":         ml["class"]         if ml else None,
        "ml_class_ru":      ml["class_ru"]      if ml else None,
        "ml_confidence":    ml["confidence"]    if ml else None,
        "ml_probabilities": ml["probabilities"] if ml else None,
        "demo":       result.get("demo", False),
        "evidence": (
            {
                "kind": "synthetic_demo",
                "source": "Deterministic local demo generator",
                "period_id": period,
                "provenance_completeness": "complete",
                "quality": {"grade": "demo", "limitation": "Not satellite evidence"},
            }
            if result.get("demo", False)
            else period_evidence(period, period_cfg, quality={"valid_pixel": True})
        ),
    }


# ════════════════════════════════════════════════════════════════
#   ZONE STATISTICS
# ════════════════════════════════════════════════════════════════


def _validate_position(position) -> tuple[float, float]:
    if not isinstance(position, (list, tuple)) or len(position) != 2:
        raise HTTPException(status_code=400, detail="Каждая координата должна иметь формат [долгота, широта]")
    lon, lat = position
    if isinstance(lon, bool) or isinstance(lat, bool) or not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        raise HTTPException(status_code=400, detail="Координаты должны быть числами")
    lon, lat = float(lon), float(lat)
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise HTTPException(status_code=400, detail="Координаты должны быть конечными числами")
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise HTTPException(status_code=400, detail="Координаты находятся вне допустимого диапазона WGS84")
    return lon, lat


def _validate_polygon_geometry(geometry: dict, allow_multi: bool = True) -> list:
    if not isinstance(geometry, dict):
        raise HTTPException(status_code=400, detail="Ожидается объект GeoJSON Polygon")
    gtype = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if gtype == "Polygon":
        polygons = [coordinates]
    elif gtype == "MultiPolygon" and allow_multi:
        polygons = coordinates
    else:
        expected = "Polygon или MultiPolygon" if allow_multi else "Polygon"
        raise HTTPException(status_code=400, detail=f"Ожидается GeoJSON {expected}")
    if not isinstance(polygons, list) or not polygons:
        raise HTTPException(status_code=400, detail="Полигон не содержит координат")

    vertex_count = 0
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise HTTPException(status_code=400, detail="Полигон должен содержать хотя бы одно кольцо")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4:
                raise HTTPException(status_code=400, detail="Кольцо полигона должно содержать минимум 4 координаты")
            validated_ring = [_validate_position(position) for position in ring]
            if validated_ring[0] != validated_ring[-1]:
                raise HTTPException(status_code=400, detail="Кольцо полигона должно быть замкнуто")
            vertex_count += len(validated_ring)
    if vertex_count > MAX_GEOMETRY_VERTICES:
        raise HTTPException(status_code=413, detail="Геометрия содержит слишком много вершин")
    return polygons


def _validate_linestring_geometry(geometry: dict) -> list[tuple[float, float]]:
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise HTTPException(status_code=400, detail="Ожидается GeoJSON LineString")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise HTTPException(status_code=400, detail="Линия должна содержать минимум 2 точки")
    if len(coordinates) > MAX_GEOMETRY_VERTICES:
        raise HTTPException(status_code=413, detail="Линия содержит слишком много вершин")
    return [_validate_position(position) for position in coordinates]


# Personal accounts stay independent from the raster-analysis implementation.
# The validator callback keeps saved polygons subject to the same geometry and
# request-size rules as immediate zonal analyses.
ACCOUNT_STORE = AccountStore(APP_DB_PATH)
ACCOUNT_MAILER = AccountMailer.from_environment(BASE_DIR)
MONITORING_SERVICE: MonitoringService | None = None


def run_monitoring_for_user(user_id: str) -> dict:
    if MONITORING_SERVICE is None:
        raise RuntimeError("monitoring service is not initialized")
    return MONITORING_SERVICE.run(user_id)


def monitoring_service_status() -> dict:
    return MONITORING_SERVICE.status() if MONITORING_SERVICE else {"enabled": False, "running": False}


app.include_router(create_account_router(
    ACCOUNT_STORE,
    allowed_origins=CORS_ORIGINS,
    validate_geometry=lambda geometry: _validate_polygon_geometry(geometry, allow_multi=False),
    secure_cookie=os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
    mailer=ACCOUNT_MAILER,
    google_client_id=GOOGLE_CLIENT_ID,
    monitoring_runner=run_monitoring_for_user,
    monitoring_status=monitoring_service_status,
))


class ZoneStatsReq(BaseModel):
    geometry: dict
    period:   str = DEFAULT_PERIOD


class ZoneTimeSeriesReq(BaseModel):
    geometry: dict


_ZONE_INDEX_KEYS = ["ndvi", "ndwi", "ndre", "ndmi", "bsi"]


def _zone_index_stats(arr: "np.ndarray") -> dict:
    """arr: 1-D float32 array of valid (already masked) index values."""
    p10, p90 = np.percentile(arr, [10, 90])
    return {
        "mean": round(float(np.mean(arr)), 4),
        "min":  round(float(np.min(arr)), 4),
        "max":  round(float(np.max(arr)), 4),
        "std":  round(float(np.std(arr)), 4),
        "p10":  round(float(p10), 4),
        "p90":  round(float(p90), 4),
    }


def _calculate_zone_stats(geometry: dict, period_id: str, include_lulc: bool = True) -> dict:
    """Calculate one period without acquiring the analysis semaphore.

    Keeping the raster work in a helper lets the time-series endpoint process
    every available annual mosaic under one bounded analysis request.
    """
    period_cfg = resolve_period(period_id)
    if not (RASTERIO_OK and cog_available(period_cfg)):
        raise HTTPException(status_code=500, detail="COG / rasterio недоступны на сервере")

    geom = geometry
    _validate_polygon_geometry(geom, allow_multi=False)

    try:
        with rasterio.open(period_cfg["cog_path"]) as ds:
            # Reproject ring(s) from WGS84 → raster CRS
            transformer = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
            rings_proj = []
            for ring in geom["coordinates"]:
                rings_proj.append([transformer.transform(lon, lat) for lon, lat in ring])

            xs = [pt[0] for ring in rings_proj for pt in ring]
            ys = [pt[1] for ring in rings_proj for pt in ring]
            minx, maxx = min(xs), max(xs)
            miny, maxy = min(ys), max(ys)

            ds_l, ds_b, ds_r, ds_t = ds.bounds
            if maxx < ds_l or minx > ds_r or maxy < ds_b or miny > ds_t:
                raise HTTPException(status_code=400, detail="Полигон находится за пределами области покрытия")

            # Clip the requested bbox to the dataset bounds before windowing
            minx, maxx = max(minx, ds_l), min(maxx, ds_r)
            miny, maxy = max(miny, ds_b), min(maxy, ds_t)

            window = window_from_bounds(minx, miny, maxx, maxy, transform=ds.transform)
            window = window.round_offsets().round_lengths()
            if window.width <= 0 or window.height <= 0:
                raise HTTPException(status_code=400, detail="Полигон слишком мал или вне покрытия")
            if int(window.width) * int(window.height) > MAX_ANALYSIS_PIXELS:
                raise HTTPException(
                    status_code=413,
                    detail="Полигон слишком большой для анализа — нарисуйте зону меньшего размера",
                )

            win_transform = ds.window_transform(window)
            data = ds.read(window=window).astype(np.float32)  # (7, h, w)

            polygon_geom = {"type": "Polygon", "coordinates": [
                [list(pt) for pt in ring] for ring in rings_proj
            ]}
            poly_mask = geometry_mask(
                [polygon_geom], out_shape=(int(window.height), int(window.width)),
                transform=win_transform, invert=True,  # True = inside polygon
            )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Внутренняя ошибка чтения растровых данных")

    nodata_mask = period_nodata_mask(data, period_cfg)
    valid_mask = poly_mask & ~nodata_mask
    geometry_pixel_count = int(np.count_nonzero(poly_mask))
    pixel_count = int(np.count_nonzero(valid_mask))
    if pixel_count == 0:
        raise HTTPException(status_code=400, detail="Нет валидных пикселей внутри полигона")

    refl = period_to_reflectance(data, period_cfg)
    b02 = refl[0][valid_mask]
    b03 = refl[1][valid_mask]
    b04 = refl[2][valid_mask]
    b05 = refl[3][valid_mask]
    b08 = refl[4][valid_mask]
    b8a = refl[5][valid_mask]
    b11 = refl[6][valid_mask]
    eps = 1e-10

    ndvi = (b08 - b04) / (b08 + b04 + eps)
    ndwi = (b03 - b08) / (b03 + b08 + eps)
    ndre = (b08 - b05) / (b08 + b05 + eps)
    ndmi = (b8a - b11) / (b8a + b11 + eps)
    bsi  = ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02) + eps)
    savi = (b08 - b04) / (b08 + b04 + 0.5 + eps) * 1.5
    nbr  = (b08 - b11) / (b08 + b11 + eps)

    indices = {
        "ndvi": _zone_index_stats(ndvi),
        "ndwi": _zone_index_stats(ndwi),
        "ndre": _zone_index_stats(ndre),
        "ndmi": _zone_index_stats(ndmi),
        "bsi":  _zone_index_stats(bsi),
        "savi": _zone_index_stats(savi),
        "nbr":  _zone_index_stats(nbr),
    }

    px_area_ha = 0.01  # 10m x 10m pixel
    lulc = {}
    if include_lulc:
        load_classifier()
    if include_lulc and CLASSIFIER is not None:
        feats = np.stack([ndvi, ndre, ndwi, ndmi, bsi, b08], axis=1).astype(np.float32)
        preds = CLASSIFIER.predict(feats)
        for i, cls in enumerate(CLASS_NAMES):
            n = int(np.count_nonzero(preds == i))
            lulc[cls] = {
                "pixels":  n,
                "area_ha": round(n * px_area_ha, 2),
                "percent": round(n / pixel_count * 100, 2) if pixel_count else 0.0,
            }

    return {
        "area_ha":     round(pixel_count * px_area_ha, 2),
        "pixel_count": pixel_count,
        "geometry_pixel_count": geometry_pixel_count,
        "indices":     indices,
        "lulc":        lulc,
        "evidence": period_evidence(
            period_id,
            period_cfg,
            quality={
                "valid_pixel_count": pixel_count,
                "geometry_pixel_count": geometry_pixel_count,
                "valid_coverage_percent": round(pixel_count / geometry_pixel_count * 100, 2)
                if geometry_pixel_count else 0.0,
            },
        ),
    }


def latest_monitoring_period() -> tuple[str, str] | None:
    """Newest available immutable mosaic and its cache-safe data version."""
    for period_id, period_cfg in reversed(list(PERIODS.items())):
        if cog_available(period_cfg):
            return period_id, period_data_version(period_cfg)
    return None


MONITORING_SERVICE = MonitoringService(
    ACCOUNT_STORE,
    ACCOUNT_MAILER,
    latest_period=latest_monitoring_period,
    calculate_stats=lambda geometry, period_id: _calculate_zone_stats(
        geometry, period_id, include_lulc=False,
    ),
    enabled=MONITORING_SCHEDULER_ENABLED,
    interval_seconds=MONITORING_INTERVAL_SECONDS,
    logger=LOGGER,
)


@app.on_event("startup")
def start_monitoring_scheduler():
    MONITORING_SERVICE.start()


@app.on_event("shutdown")
def stop_monitoring_scheduler():
    MONITORING_SERVICE.stop()


@app.post("/api/zone_stats")
@limit_analysis
def zone_stats(req: ZoneStatsReq):
    return _calculate_zone_stats(req.geometry, req.period)


@app.post("/api/zone_timeseries")
@limit_analysis
def zone_timeseries(req: ZoneTimeSeriesReq):
    """Return comparable annual summaries for every available COG period."""
    _validate_polygon_geometry(req.geometry, allow_multi=False)
    observations = []
    for period_id, period_cfg in PERIODS.items():
        year = _period_year(period_id)
        if year is None or not cog_available(period_cfg):
            continue
        stats = _calculate_zone_stats(req.geometry, period_id, include_lulc=False)
        observations.append({
            "year": year,
            "period_id": period_id,
            "label": period_cfg["label"],
            "date_range": period_cfg["date_range"],
            "area_ha": stats["area_ha"],
            "pixel_count": stats["pixel_count"],
            "indices": stats["indices"],
            "evidence": stats["evidence"],
        })

    observations.sort(key=lambda item: item["year"])
    if not observations:
        raise HTTPException(status_code=503, detail="Нет доступных периодов для временного ряда")

    baselines = {}
    for index in LAYERS:
        values = np.asarray([
            item["indices"][index]["mean"]
            for item in observations
            if item.get("indices", {}).get(index, {}).get("mean") is not None
        ], dtype=np.float32)
        if values.size == 0:
            continue
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        baselines[index] = {
            "median": round(median, 4),
            "mean": round(float(np.mean(values)), 4),
            "std": round(float(np.std(values)), 4),
            "mad": round(mad, 4),
            "observation_count": int(values.size),
        }
        for item in observations:
            value = item.get("indices", {}).get(index, {}).get("mean")
            item.setdefault("anomalies", {})[index] = (
                round(float(value) - median, 4) if value is not None else None
            )
    return {
        "observations": observations,
        "years": [item["year"] for item in observations],
        "baselines": baselines,
        "anomaly_method": "difference_from_available_period_median",
        "source": f"{S2_PRODUCT} / {S2_SOURCE}",
    }


# ════════════════════════════════════════════════════════════════
#   TRANSECT (line profile)
# ════════════════════════════════════════════════════════════════

_TRANSECT_LAYERS = {"ndvi", "ndwi", "ndre", "ndmi", "bsi", "savi", "nbr"}
_TRANSECT_MAX_POINTS = 5000


class TransectReq(BaseModel):
    geometry: dict
    layer:    str
    period:   str = DEFAULT_PERIOD


def _transect_index(layer: str, b02, b03, b04, b05, b08, b8a, b11, eps: float):
    if layer == "ndvi": return (b08 - b04) / (b08 + b04 + eps)
    if layer == "ndwi": return (b03 - b08) / (b03 + b08 + eps)
    if layer == "ndre": return (b08 - b05) / (b08 + b05 + eps)
    if layer == "ndmi": return (b8a - b11) / (b8a + b11 + eps)
    if layer == "bsi":  return ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02) + eps)
    if layer == "savi": return (b08 - b04) / (b08 + b04 + 0.5 + eps) * 1.5
    if layer == "nbr":  return (b08 - b11) / (b08 + b11 + eps)
    raise ValueError(f"unknown layer {layer}")


@app.post("/api/transect")
@limit_analysis
def transect(req: TransectReq):
    period_cfg = resolve_period(req.period)
    if not (RASTERIO_OK and cog_available(period_cfg)):
        raise HTTPException(status_code=500, detail="COG / rasterio недоступны на сервере")

    if req.layer not in _TRANSECT_LAYERS:
        raise HTTPException(status_code=400, detail=f"layer должен быть одним из: {sorted(_TRANSECT_LAYERS)}")

    coords = _validate_linestring_geometry(req.geometry)

    try:
        with rasterio.open(period_cfg["cog_path"]) as ds:
            transformer = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
            verts_proj = [transformer.transform(lon, lat) for lon, lat in coords]

            xs = [p[0] for p in verts_proj]
            ys = [p[1] for p in verts_proj]
            ds_l, ds_b, ds_r, ds_t = ds.bounds
            if max(xs) < ds_l or min(xs) > ds_r or max(ys) < ds_b or min(ys) > ds_t:
                raise HTTPException(status_code=400, detail="Линия находится за пределами области покрытия")

            px = abs(ds.transform.a)  # pixel size in meters (10m)

            # cumulative segment lengths
            seg_lengths = []
            total_length = 0.0
            for i in range(len(verts_proj) - 1):
                x1, y1 = verts_proj[i]
                x2, y2 = verts_proj[i + 1]
                d = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                seg_lengths.append(d)
                total_length += d
            if total_length <= 0:
                raise HTTPException(status_code=400, detail="Длина линии равна нулю")

            step = px
            n_samples = int(total_length / step) + 1
            if n_samples > _TRANSECT_MAX_POINTS:
                step = total_length / _TRANSECT_MAX_POINTS
                n_samples = _TRANSECT_MAX_POINTS

            sample_xy_proj = []   # (x, y) in raster CRS
            sample_lonlat  = []   # (lon, lat)
            sample_dist    = []
            transformer_inv = Transformer.from_crs(ds.crs, "EPSG:4326", always_xy=True)

            d = 0.0
            seg_idx = 0
            seg_start_dist = 0.0
            while d <= total_length + 1e-6:
                dd = min(d, total_length)
                while seg_idx < len(seg_lengths) - 1 and dd > seg_start_dist + seg_lengths[seg_idx] + 1e-9:
                    seg_start_dist += seg_lengths[seg_idx]
                    seg_idx += 1
                seg_len = seg_lengths[seg_idx] or 1e-9
                t = (dd - seg_start_dist) / seg_len
                t = max(0.0, min(1.0, t))
                x1, y1 = verts_proj[seg_idx]
                x2, y2 = verts_proj[seg_idx + 1]
                x = x1 + (x2 - x1) * t
                y = y1 + (y2 - y1) * t
                lon, lat = transformer_inv.transform(x, y)
                sample_xy_proj.append((x, y))
                sample_lonlat.append((lon, lat))
                sample_dist.append(dd)
                d += step

            samples = list(ds.sample(sample_xy_proj))  # list of (7,) arrays
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Внутренняя ошибка чтения растровых данных")

    eps = 1e-10
    points = []
    valid_values = []
    for dist, (lon, lat), band_vals in zip(sample_dist, sample_lonlat, samples):
        band_vals = band_vals.astype(np.float32)
        if period_nodata_mask(band_vals.reshape(7, 1, 1), period_cfg).all():
            points.append({"distance_m": round(dist, 2), "value": None, "lon": round(lon, 6), "lat": round(lat, 6)})
            continue
        b02, b03, b04, b05, b08, b8a, b11 = period_to_reflectance(band_vals, period_cfg)
        value = float(_transect_index(req.layer, b02, b03, b04, b05, b08, b8a, b11, eps))
        points.append({"distance_m": round(dist, 2), "value": round(value, 4), "lon": round(lon, 6), "lat": round(lat, 6)})
        valid_values.append(value)

    if not valid_values:
        raise HTTPException(status_code=400, detail="Нет валидных пикселей вдоль линии")

    stats = {
        "min":  round(float(np.min(valid_values)), 4),
        "max":  round(float(np.max(valid_values)), 4),
        "mean": round(float(np.mean(valid_values)), 4),
    }

    return {
        "layer":          req.layer,
        "total_length_m": round(total_length, 2),
        "points":         points,
        "stats":          stats,
        "evidence": period_evidence(
            req.period,
            period_cfg,
            quality={
                "valid_sample_count": len(valid_values),
                "requested_sample_count": len(points),
                "valid_coverage_percent": round(len(valid_values) / len(points) * 100, 2),
            },
        ),
    }


# ════════════════════════════════════════════════════════════════
#   AI ANALYSIS
# ════════════════════════════════════════════════════════════════

class AnalyzeReq(BaseModel):
    lat:        float       = Field(..., ge=-90.0, le=90.0)
    lon:        float       = Field(..., ge=-180.0, le=180.0)
    period:     str         = DEFAULT_PERIOD
    ndvi:       float | None = None
    ndwi:       float | None = None
    ndre:       float | None = None
    ndmi:       float | None = None
    bsi:        float | None = None
    savi:       float | None = None
    nbr:        float | None = None
    ml_class:       str   | None = None
    ml_class_ru:    str   | None = None
    ml_confidence:  float | None = None
    locale:          str = Field(default="ru", pattern=r"^(ru|kk|en)$")


AI_LANGUAGE_NAMES = {"ru": "русском", "kk": "казахском", "en": "английском"}


# Expected index ranges per ML land-cover class for this region — lets the
# prompt flag real deviations (water stress, salinization, degradation)
# instead of just restating the classification.
REGIONAL_NORMS = {
    "agriculture":       {"ndvi": (0.35, 0.65), "ndmi": (0.10, 0.40)},
    "sparse_vegetation": {"ndvi": (0.05, 0.20), "ndmi": (-0.20, 0.05)},
    "bare_soil":         {"ndvi": (-0.05, 0.10), "bsi": (0.10, 0.35)},
    "water":             {"ndwi": (0.20, 0.60)},
    "urban":             {"bsi": (0.05, 0.25), "ndvi": (-0.05, 0.15)},
    "dense_vegetation":  {"ndvi": (0.45, 0.75), "ndmi": (0.15, 0.50)},
}


def build_groq_prompt(lat, lon, period, indices, ml_class, ml_class_ru, ml_confidence, locale="ru"):
    """Builds the analysis prompt around deviations from this region's normal
    index ranges for the ML-predicted class, rather than just restating it."""
    ndvi, ndwi, ndmi, bsi, ndre, savi, nbr = (
        indices.get(k) for k in ("NDVI", "NDWI", "NDMI", "BSI", "NDRE", "SAVI", "NBR")
    )
    period_label = resolve_period(period)["label"].lower()
    norms = REGIONAL_NORMS.get(ml_class, {})

    warnings = []
    recommendations = []

    if ml_class == "agriculture":
        ndvi_norm = norms.get("ndvi", (0.35, 0.65))
        ndmi_norm = norms.get("ndmi", (0.10, 0.40))

        if ndvi is not None and ndvi < ndvi_norm[0]:
            deficit = round((ndvi_norm[0] - ndvi) / ndvi_norm[0] * 100)
            warnings.append(f"NDVI={ndvi:.3f} ниже нормы ({ndvi_norm[0]}-{ndvi_norm[1]}) на {deficit}%")
            recommendations.append("проверить состояние посевов, возможен дефицит питания")

        if ndmi is not None and ndmi < ndmi_norm[0]:
            warnings.append(f"NDMI={ndmi:.3f} указывает на недостаток влаги")
            recommendations.append("увеличить полив или проверить ирригационные каналы")

        if bsi is not None and bsi > 0.15:
            warnings.append(f"BSI={bsi:.3f} повышен — возможно засоление почвы")
            recommendations.append("провести анализ почвы на засоление")

    elif ml_class == "water":
        if ndwi is not None and ndwi < 0.3:
            warnings.append(f"NDWI={ndwi:.3f} низкий для водного объекта — возможно обмеление")
            recommendations.append("мониторить уровень воды")

    elif ml_class == "bare_soil":
        if bsi is not None and bsi > 0.25:
            warnings.append(f"BSI={bsi:.3f} критически высокий — сильная деградация")
            recommendations.append("необходима рекультивация, посадка защитных полос")
        if ndre is not None and ndre > 0.1:
            warnings.append("есть потенциал для восстановления растительности")

    warnings_text = "; ".join(warnings) if warnings else "отклонений от нормы не выявлено"

    def fmt(v):
        return f"{v:.3f}" if v is not None else "н/д"

    class_label = (ml_class_ru if locale == "ru" else ml_class) or "неизвестен"
    confidence_pct = round((ml_confidence or 0) * 100)
    response_language = AI_LANGUAGE_NAMES.get(locale, AI_LANGUAGE_NAMES["ru"])

    return f"""Ты агроэколог и эксперт по землепользованию Туркестанской области Казахстана.

Данные Sentinel-2 для точки {lat:.4f}°N, {lon:.4f}°E ({period_label}):
- Тип покрова: {class_label} (уверенность {confidence_pct}%)
- NDVI={fmt(ndvi)}, NDRE={fmt(ndre)}, NDWI={fmt(ndwi)}, NDMI={fmt(ndmi)}, BSI={fmt(bsi)}, SAVI={fmt(savi)}, NBR={fmt(nbr)}
- Выявленные отклонения: {warnings_text}

Напиши 2-3 предложения на {response_language} языке:
1. Конкретная проблема или состояние (не описывай то что уже известно из класса)
2. Практическая рекомендация для землепользователя или агронома
3. Риск если не принять меры (только если есть реальная проблема)

Будь конкретным и практичным. Не повторяй класс покрова."""


@app.post("/api/analyze")
@limit_analysis
def analyze(req: AnalyzeReq):
    period_cfg = resolve_period(req.period)
    validate_point_coverage(req.lat, req.lon, period_cfg)
    indices = {
        "NDVI": req.ndvi, "NDWI": req.ndwi, "NDRE": req.ndre,
        "NDMI": req.ndmi, "BSI": req.bsi, "SAVI": req.savi, "NBR": req.nbr,
    }
    prompt = build_groq_prompt(
        req.lat, req.lon, req.period, indices,
        req.ml_class, req.ml_class_ru, req.ml_confidence, req.locale,
    )
    response_language = AI_LANGUAGE_NAMES.get(req.locale, AI_LANGUAGE_NAMES["ru"])
    system = ("Эксперт по дистанционному зондированию Центральной Азии. "
              f"Анализируешь Sentinel-2. Отвечай кратко и конкретно на {response_language} языке.")

    if AI_OK:
        for env, url, model in [
            ("GROQ_API_KEY",    "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
            ("DEEPSEEK_API_KEY","https://api.deepseek.com",        "deepseek-chat"),
        ]:
            key = os.getenv(env)
            if not key: continue
            try:
                r = ai_client(key, url).chat.completions.create(
                    model=model, max_tokens=280, temperature=0.3,
                    messages=[{"role":"system","content":system},
                               {"role":"user","content":prompt}])
                return {"analysis": r.choices[0].message.content,
                        "source": env.split("_")[0].lower(), "model": model}
            except Exception as e:
                print(f"AI error ({env}): {e}")

    # Local fallback
    ndvi = req.ndvi
    if ndvi is None:
        unavailable = {
            "ru": "Данные недоступны — выберите другую точку.",
            "kk": "Деректер қолжетімсіз — басқа нүктені таңдаңыз.",
            "en": "Data is unavailable — select another point.",
        }
        return {"analysis": unavailable[req.locale], "source":"local", "model":"local"}
    local_copy = {
        "ru": (
            f"NDVI={ndvi:.2f} — активная густая растительность. Вероятно ирригированные поля или пойма Сырдарьи. Состояние хорошее.",
            f"NDVI={ndvi:.2f} — умеренная растительность. Характерно для пастбищ или полей в начале вегетационного сезона.",
            f"NDVI={ndvi:.2f} — слабая растительность. Пустынные или деградированные земли. Возможны засоление и опустынивание.",
        ),
        "kk": (
            f"NDVI={ndvi:.2f} — белсенді қалың өсімдік. Бұл суармалы егістік немесе Сырдария жайылмасы болуы мүмкін. Жағдайы жақсы.",
            f"NDVI={ndvi:.2f} — өсімдік деңгейі орташа. Жайылымдарға немесе вегетация басындағы егістіктерге тән.",
            f"NDVI={ndvi:.2f} — өсімдік сирек. Шөлді немесе тозған жерлер. Тұздану мен шөлейттену қаупі бар.",
        ),
        "en": (
            f"NDVI={ndvi:.2f} indicates active, dense vegetation, likely irrigated fields or the Syr Darya floodplain. Conditions appear good.",
            f"NDVI={ndvi:.2f} indicates moderate vegetation, typical of pasture or fields early in the growing season.",
            f"NDVI={ndvi:.2f} indicates sparse vegetation and potentially desertified or degraded land. Salinization and desertification are possible risks.",
        ),
    }
    high, medium, low = local_copy[req.locale]
    if   ndvi > 0.5:  txt = high
    elif ndvi > 0.25: txt = medium
    else:             txt = low
    return {"analysis": txt, "source": "local", "model": "local"}


# ════════════════════════════════════════════════════════════════
#   ZONE REPORT (Groq) — PDF is assembled on the frontend
# ════════════════════════════════════════════════════════════════

class ZoneReportReq(BaseModel):
    geometry:         dict
    zone_stats:       dict
    active_layer:     str | None = None
    period:           str = DEFAULT_PERIOD
    locale:           str = Field(default="ru", pattern=r"^(ru|kk|en)$")


_LULC_LABELS_RU = {
    "agriculture":       "Сельхоз угодья",
    "urban":             "Застройка",
    "dense_vegetation":  "Густая растительность",
    "sparse_vegetation": "Разреженная растительность",
    "bare_soil":         "Голая почва",
    "water":             "Водные объекты",
}
_LULC_ORDER = ["agriculture", "urban", "dense_vegetation", "sparse_vegetation", "bare_soil", "water"]


def build_zone_report_prompt(stats: dict, active_layer: str | None, period: str, locale: str = "ru") -> str:
    """Detailed Russian prompt for a 4-section structured zone report (vs. the
    short 2-3 sentence pixel-level prompt in build_groq_prompt above)."""
    area_ha = stats.get("area_ha") or 0
    idx     = stats.get("indices") or {}
    lulc    = stats.get("lulc") or {}
    period_label = resolve_period(period)["label"]

    def i(key, field):
        v = (idx.get(key) or {}).get(field)
        return f"{v:.3f}" if isinstance(v, (int, float)) else "н/д"

    lulc_lines = "\n".join(
        f"- {_LULC_LABELS_RU.get(k, k)}: {(lulc.get(k) or {}).get('area_ha', 0):.2f} га "
        f"({(lulc.get(k) or {}).get('percent', 0):.2f}%)"
        for k in _LULC_ORDER if k in lulc
    ) or "- данные классификации отсутствуют"

    response_language = AI_LANGUAGE_NAMES.get(locale, AI_LANGUAGE_NAMES["ru"])
    return f"""Ты — эксперт по дистанционному зондированию и агрономии Казахстана. Проанализируй спутниковые данные Sentinel-2 для зоны в Туркестанской области.

ДАННЫЕ ЗОНЫ:
- Общая площадь: {area_ha:.2f} га
- Период съёмки: {period_label}
- Активный слой при анализе: {active_layer or "ndvi"}

СПЕКТРАЛЬНЫЕ ИНДЕКСЫ (среднее по зоне):
- NDVI (растительность): {i('ndvi','mean')} (диапазон: {i('ndvi','min')} — {i('ndvi','max')})
- NDWI (водные ресурсы): {i('ndwi','mean')}
- NDRE (стресс растений): {i('ndre','mean')}
- NDMI (влажность почвы): {i('ndmi','mean')}
- BSI (голая почва): {i('bsi','mean')}

КЛАССИФИКАЦИЯ ЗЕМЕЛЬ:
{lulc_lines}

Напиши структурированный отчёт из 4 разделов:
1. ОБЩАЯ ХАРАКТЕРИСТИКА ЗОНЫ — опиши что представляет собой территория исходя из данных
2. СОСТОЯНИЕ РАСТИТЕЛЬНОСТИ И ПОЧВ — интерпретируй индексы, укажи проблемные зоны
3. ЗЕМЛЕПОЛЬЗОВАНИЕ — проанализируй соотношение классов, есть ли аномалии
4. РЕКОМЕНДАЦИИ — конкретные агрономические и управленческие меры

Пиши профессионально, но понятно, на {response_language} языке. Каждый раздел 3-4 предложения.
Заголовок каждого раздела пиши ровно в формате "N. НАЗВАНИЕ" заглавными буквами на отдельной строке, без markdown-разметки (без звёздочек, решёток и слова "Раздел")."""


def _local_zone_report(stats: dict, locale: str = "ru") -> str:
    """Template fallback used only if Groq is unreachable/unconfigured."""
    area_ha = stats.get("area_ha") or 0
    idx     = stats.get("indices") or {}
    lulc    = stats.get("lulc") or {}
    ndvi    = (idx.get("ndvi") or {}).get("mean")
    top_class = max(lulc.items(), key=lambda kv: kv[1].get("area_ha", 0))[0] if lulc else None
    top_label = _LULC_LABELS_RU.get(top_class, top_class or "неизвестно")
    ndvi_text = f"{ndvi:.3f}" if ndvi is not None else "н/д"

    if locale == "en":
        return (
            f"1. GENERAL ZONE CHARACTERISTICS\nThe {area_ha:.2f} ha zone is predominantly classified as “{top_class or 'unknown'}”. "
            f"Mean NDVI is {ndvi_text}.\n\n2. VEGETATION AND SOIL CONDITION\n"
            "AI analysis is temporarily unavailable; this section uses a local template. Configure GROQ_API_KEY for a full assessment.\n\n"
            "3. LAND USE\nThe land-cover classification is shown in the table above.\n\n"
            "4. RECOMMENDATIONS\nGenerate the report again when the AI service is available."
        )
    if locale == "kk":
        return (
            f"1. АЙМАҚТЫҢ ЖАЛПЫ СИПАТТАМАСЫ\nАуданы {area_ha:.2f} га аймақта «{top_class or 'белгісіз'}» санаты басым. "
            f"Орташа NDVI мәні {ndvi_text}.\n\n2. ӨСІМДІК ПЕН ТОПЫРАҚ ЖАҒДАЙЫ\n"
            "AI талдауы уақытша қолжетімсіз; бұл бөлім жергілікті үлгімен жасалды. Толық қорытынды үшін GROQ_API_KEY баптаңыз.\n\n"
            "3. ЖЕР ПАЙДАЛАНУ\nЖер жамылғысының жіктеуі жоғарыдағы кестеде берілген.\n\n"
            "4. ҰСЫНЫСТАР\nAI қызметі қолжетімді болғанда есепті қайта жасаңыз."
        )
    return (
        f"1. ОБЩАЯ ХАРАКТЕРИСТИКА ЗОНЫ\n"
        f"Зона площадью {area_ha:.2f} га преимущественно представлена классом «{top_label}». "
        f"Среднее значение NDVI составляет {ndvi_text}.\n\n"
        f"2. СОСТОЯНИЕ РАСТИТЕЛЬНОСТИ И ПОЧВ\n"
        f"Автоматический анализ ИИ временно недоступен — раздел сформирован по шаблону. "
        f"Рекомендуется проверить ключ GROQ_API_KEY на сервере для получения полного заключения.\n\n"
        f"3. ЗЕМЛЕПОЛЬЗОВАНИЕ\n"
        f"Классификация земель приведена в таблице выше.\n\n"
        f"4. РЕКОМЕНДАЦИИ\n"
        f"Повторите генерацию отчёта позже, когда AI-сервис будет доступен."
    )


@app.post("/api/zone_report")
@limit_analysis
def zone_report(req: ZoneReportReq):
    _validate_polygon_geometry(req.geometry, allow_multi=False)
    resolve_period(req.period)
    area_ha = req.zone_stats.get("area_ha") if isinstance(req.zone_stats, dict) else None
    if isinstance(area_ha, bool) or not isinstance(area_ha, (int, float)) or not math.isfinite(float(area_ha)):
        raise HTTPException(status_code=400, detail="zone_stats.area_ha должен быть конечным числом")
    if not isinstance(req.zone_stats.get("indices"), dict) or not isinstance(req.zone_stats.get("lulc"), dict):
        raise HTTPException(status_code=400, detail="zone_stats должен содержать объекты indices и lulc")
    prompt = build_zone_report_prompt(req.zone_stats, req.active_layer, req.period, req.locale)
    response_language = AI_LANGUAGE_NAMES.get(req.locale, AI_LANGUAGE_NAMES["ru"])
    system = ("Эксперт по дистанционному зондированию и агрономии Центральной Азии. "
              f"Пишешь развёрнутые структурированные аналитические отчёты на {response_language} языке.")

    if AI_OK:
        key = os.getenv("GROQ_API_KEY")
        if key:
            try:
                # llama3-8b-8192 was decommissioned by Groq — llama-3.1-8b-instant is
                # its direct successor in the same fast/cheap 8B tier.
                r = ai_client(key, "https://api.groq.com/openai/v1").chat.completions.create(
                    model="llama-3.1-8b-instant", max_tokens=1500, temperature=0.4,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": prompt}])
                return {"groq_analysis": r.choices[0].message.content, "model": "llama-3.1-8b-instant"}
            except Exception as e:
                print(f"Zone report AI error: {e}")

    return {"groq_analysis": _local_zone_report(req.zone_stats, req.locale), "model": "local"}


# ════════════════════════════════════════════════════════════════
#   CHANGE DETECTION — shared helpers (used by /api/change_stats and
#   the /api/change_overview background precompute)
# ════════════════════════════════════════════════════════════════

_CHANGE_INDEX_KEYS = ["ndvi", "ndwi", "ndre", "ndmi", "bsi", "savi", "nbr"]
_DIRECTION_EPS = 0.005  # noise floor (index units) below which mean delta reads as "стабильно"


def _all_indices_from_refl(refl: "np.ndarray") -> dict:
    """refl: (7, h, w) physical reflectance -> dict of the 7 index arrays,
    same formulas as compute_index(), vectorized over the whole array at once."""
    b02, b03, b04, b05, b08, b8a, b11 = refl
    eps = 1e-10
    return {
        "ndvi": (b08 - b04) / (b08 + b04 + eps),
        "ndwi": (b03 - b08) / (b03 + b08 + eps),
        "ndre": (b08 - b05) / (b08 + b05 + eps),
        "ndmi": (b8a - b11) / (b8a + b11 + eps),
        "bsi":  ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02) + eps),
        "savi": (b08 - b04) / (b08 + b04 + 0.5 + eps) * 1.5,
        "nbr":  (b08 - b11) / (b08 + b11 + eps),
    }


def _finalize_direction(index: str, mean_before: float, mean_after: float,
                        mean_delta: float, std_delta: float, significant_pct: float) -> dict:
    signed = mean_delta * DIRECTION_SIGN.get(index, 1)
    if abs(signed) < _DIRECTION_EPS:
        direction = "стабильно"
    elif signed > 0:
        direction = "улучшение"
    else:
        direction = "деградация"
    return {
        "mean_before":      round(mean_before, 4),
        "mean_after":       round(mean_after, 4),
        "delta":            round(mean_delta, 4),
        "std_delta":        round(std_delta, 4),
        "significant_pct":  round(significant_pct, 2),
        "direction":        direction,
    }


def _change_index_stats_from_arrays(index: str, before_vals: "np.ndarray", after_vals: "np.ndarray") -> dict:
    """Single-pass version (Task 2) — the whole zone's values already fit in memory."""
    delta = after_vals - before_vals
    mean_before = float(np.mean(before_vals))
    mean_after  = float(np.mean(after_vals))
    mean_delta  = float(np.mean(delta))
    std_delta   = float(np.std(delta))
    significant_pct = float(np.mean(np.abs(delta) > 1.5 * std_delta) * 100) if std_delta > 0 else 0.0
    return _finalize_direction(index, mean_before, mean_after, mean_delta, std_delta, significant_pct)


def _normalize_geometry_polygons(geometry: dict) -> list:
    """Polygon -> [rings]; MultiPolygon -> [rings_of_poly1, rings_of_poly2, ...].
    MultiPolygon support exists for the official oblast boundary (Task 3's
    /api/change_overview); hand-drawn zones from the frontend are always a
    plain Polygon, handled as the trivial single-polygon case."""
    return _validate_polygon_geometry(geometry, allow_multi=True)


def _project_polygons(polygons: list, transformer: "Transformer") -> list:
    return [[[transformer.transform(lon, lat) for lon, lat in ring] for ring in poly] for poly in polygons]


def _polygons_bounds(polygons_proj: list) -> tuple:
    xs = [pt[0] for poly in polygons_proj for ring in poly for pt in ring]
    ys = [pt[1] for poly in polygons_proj for ring in poly for pt in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _polygons_geometry_mask(polygons_proj: list, out_shape: tuple, win_transform) -> "np.ndarray":
    geoms = [{"type": "Polygon", "coordinates": [[list(pt) for pt in ring] for ring in poly]} for poly in polygons_proj]
    return geometry_mask(geoms, out_shape=out_shape, transform=win_transform, invert=True)


def _read_padded_window(ds, window: "Window", cfg: dict) -> "np.ndarray":
    """Read `window` padded by 1px on every side via a boundless read, filled
    with the period's own nodata value wherever the padding falls outside the
    raster's true extent. This gives the 3x3 texture-std computation real
    neighbour pixels at a polygon/block edge instead of a fabricated zero —
    it only degrades to "fewer valid neighbours" (same as classify_ml_v2's
    per-point window) at the raster's actual boundary."""
    fill = _REFLECTANCE_NODATA if cfg["storage"] == "reflectance" else 0
    padded = Window(window.col_off - 1, window.row_off - 1, window.width + 2, window.height + 2)
    return ds.read(window=padded, boundless=True, fill_value=fill).astype(np.float32)


def _windowed_std_valid(band: "np.ndarray", valid: "np.ndarray", size: int = 3) -> "np.ndarray":
    """Per-pixel std over a size x size neighbourhood, counting only valid
    pixels — nodata and out-of-array positions are excluded from the
    mean/variance rather than treated as real zero-valued data. This
    reproduces extract_samples_v3.py / classify_ml_v2's per-point texture
    window (edge-clipped, nodata-excluded std) as a vectorized boxcar
    instead of a Python loop per point: uniform_filter's mode='constant'
    zero-padding is applied to the *masked* values and to the validity mask,
    not the raw band — so it only ever contributes 0 to both the sum and the
    neighbour count, which is mathematically the same as excluding that
    neighbour entirely.
    """
    band = band.astype(np.float64)
    masked = np.where(valid, band, 0.0)
    valid_f = valid.astype(np.float64)
    n = size * size
    s   = uniform_filter(masked,      size=size, mode="constant", cval=0.0) * n
    s2  = uniform_filter(masked ** 2, size=size, mode="constant", cval=0.0) * n
    cnt = np.round(uniform_filter(valid_f, size=size, mode="constant", cval=0.0) * n)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = s / cnt
        var  = s2 / cnt - mean ** 2
    std = np.sqrt(np.clip(var, 0, None))
    std[cnt < 3] = 0.0
    return std.astype(np.float32)


def _v3_texture_stds(refl_padded: "np.ndarray", valid_padded: "np.ndarray") -> list:
    """refl_padded: (7, h+2, w+2) reflectance bands; valid_padded: (h+2, w+2)
    bool. Returns 7 std maps cropped back to (h, w), band order B02..B11."""
    return [_windowed_std_valid(refl_padded[i], valid_padded)[1:-1, 1:-1] for i in range(7)]


def _build_v3_features(idx_map: dict, refl: "np.ndarray", stds: list) -> "np.ndarray":
    """Stacks the 13 features in exactly lulc_classifier_v3.pkl's feature_names
    order: ndvi, ndre, ndwi, ndmi, bsi, b08, std_b02..std_b11 (verified against
    the pickle directly, not just the training script's comment)."""
    b08 = refl[4]
    return np.stack([idx_map["ndvi"], idx_map["ndre"], idx_map["ndwi"], idx_map["ndmi"],
                      idx_map["bsi"], b08, *stds], axis=0)


def _predict_v3_encoded(idx_map: dict, refl: "np.ndarray", stds: list, valid_mask: "np.ndarray"):
    """Returns int64 label-encoder-space predictions for valid_mask pixels
    (flattened in np.nonzero(valid_mask) order), or None if v3 isn't loaded.
    model.predict() already returns label_encoder-space ints directly
    (verified empirically) — CLASS_NAMES_V2 is that same encoder's classes_,
    so no inverse_transform round-trip is needed for the transition matrix."""
    if CLASSIFIER_V2 is None or LABEL_ENCODER_V2 is None:
        return None
    feats = _build_v3_features(idx_map, refl, stds)
    X = feats[:, valid_mask].T.astype(np.float32)
    if X.shape[0] == 0:
        return np.array([], dtype=np.int64)
    return CLASSIFIER_V2.predict(X).astype(np.int64)


def _transitions_from_counts(counts: "np.ndarray", pixel_count: int, px_area_ha: float = 0.01) -> dict:
    """counts: (n,n) int matrix, counts[i,j] = pixels classified class `i` in
    the before period and class `j` in the after period, CLASS_NAMES_V2 order
    on both axes. Shared response shape for /api/change_stats and
    /api/change_overview."""
    n = counts.shape[0]
    names = CLASS_NAMES_V2
    matrix = {names[i]: {names[j]: int(counts[i, j]) for j in range(n)} for i in range(n)}

    off_diag = [(i, j, int(counts[i, j])) for i in range(n) for j in range(n) if i != j and counts[i, j] > 0]
    off_diag.sort(key=lambda t: t[2], reverse=True)
    top_changes = [
        {
            "from": names[i], "to": names[j],
            "pixels": px, "area_ha": round(px * px_area_ha, 2),
            "pct_of_zone": round(px / pixel_count * 100, 2) if pixel_count else 0.0,
        }
        for i, j, px in off_diag[:5]
    ]

    area_before = counts.sum(axis=1) * px_area_ha
    area_after  = counts.sum(axis=0) * px_area_ha
    net_change_ha = {names[i]: round(float(area_after[i] - area_before[i]), 2) for i in range(n)}

    return {"matrix": matrix, "top_changes": top_changes, "net_change_ha": net_change_ha}


def build_change_stats_prompt(stats: dict, locale: str = "ru") -> str:
    idx = stats.get("indices") or {}
    ml  = stats.get("ml_transitions") or {}
    top = ml.get("top_changes") or []

    def fmt(key, field):
        v = (idx.get(key) or {}).get(field)
        return f"{v:.3f}" if isinstance(v, (int, float)) else "н/д"

    idx_lines = "\n".join(
        f"- {key.upper()}: было {fmt(key,'mean_before')}, стало {fmt(key,'mean_after')}, "
        f"Δ={fmt(key,'delta')} ({(idx.get(key) or {}).get('direction','н/д')}, "
        f"значимо на {(idx.get(key) or {}).get('significant_pct', 0):.1f}% площади)"
        for key in _CHANGE_INDEX_KEYS
    )

    top_lines = "\n".join(
        f"- {t['from']} → {t['to']}: {t['area_ha']:.2f} га ({t['pct_of_zone']:.1f}% зоны)"
        for t in top
    ) or "- значимых переходов классов не обнаружено"

    response_language = AI_LANGUAGE_NAMES.get(locale, AI_LANGUAGE_NAMES["ru"])
    return f"""Ты — эксперт по дистанционному зондированию и землепользованию Туркестанской области Казахстана.

Сравниваются два периода спутниковых снимков Sentinel-2: {stats.get('period_before')} и {stats.get('period_after')}.
Площадь зоны: {stats.get('area_ha', 0):.2f} га.

ИЗМЕНЕНИЯ ИНДЕКСОВ:
{idx_lines}

ТОП ПЕРЕХОДОВ ЗЕМЕЛЬНОГО ПОКРОВА (ML-классификация):
{top_lines}

Напиши 3-4 предложения на {response_language} языке с агрономической/экологической интерпретацией
произошедших изменений: что реально случилось на территории, какие риски это несёт,
и какие меры стоит принять. Будь конкретным, не пересказывай цифры — объясняй их смысл."""


# ════════════════════════════════════════════════════════════════
#   CHANGE STATS — zonal change detection for a drawn polygon
# ════════════════════════════════════════════════════════════════

_CHANGE_STATS_MAX_PIXELS = MAX_ANALYSIS_PIXELS


class ChangeStatsReq(BaseModel):
    geometry:      dict
    period_before: str = "2023_summer"
    period_after:  str = "2025_summer"
    locale:        str = Field(default="ru", pattern=r"^(ru|kk|en)$")


def _compute_change_stats(geometry: dict, period_before: str, period_after: str) -> dict:
    load_classifier_v2()
    if period_before == period_after:
        raise HTTPException(status_code=400, detail="Для анализа изменений выберите разные периоды")
    before_cfg = resolve_period(period_before)
    after_cfg  = resolve_period(period_after)
    if not (RASTERIO_OK and cog_available(before_cfg) and cog_available(after_cfg)):
        raise HTTPException(status_code=500, detail="COG / rasterio недоступны на сервере")

    polygons = _normalize_geometry_polygons(geometry)

    try:
        with rasterio.open(before_cfg["cog_path"]) as ds_before, rasterio.open(after_cfg["cog_path"]) as ds_after:
            if ds_before.crs != ds_after.crs:
                raise HTTPException(status_code=500, detail="Периоды используют разные системы координат")

            transformer = Transformer.from_crs("EPSG:4326", ds_before.crs, always_xy=True)
            polygons_proj = _project_polygons(polygons, transformer)
            minx, miny, maxx, maxy = _polygons_bounds(polygons_proj)

            ds_l, ds_b, ds_r, ds_t = ds_before.bounds
            if maxx < ds_l or minx > ds_r or maxy < ds_b or miny > ds_t:
                raise HTTPException(status_code=400, detail="Полигон находится за пределами области покрытия")
            minx, maxx = max(minx, ds_l), min(maxx, ds_r)
            miny, maxy = max(miny, ds_b), min(maxy, ds_t)

            window = window_from_bounds(minx, miny, maxx, maxy, transform=ds_before.transform)
            window = window.round_offsets().round_lengths()
            if window.width <= 0 or window.height <= 0:
                raise HTTPException(status_code=400, detail="Полигон слишком мал или вне покрытия")
            if window.width * window.height > _CHANGE_STATS_MAX_PIXELS:
                raise HTTPException(status_code=413,
                                    detail="Полигон слишком большой для этого анализа — нарисуйте зону меньшего размера")

            window_after = window_from_bounds(minx, miny, maxx, maxy, transform=ds_after.transform)
            window_after = window_after.round_offsets().round_lengths()
            if (window.width, window.height) != (window_after.width, window_after.height):
                raise HTTPException(status_code=500, detail="Мозаики периодов не выровнены по сетке пикселей")

            win_transform = ds_before.window_transform(window)
            h, w = int(window.height), int(window.width)
            poly_mask = _polygons_geometry_mask(polygons_proj, (h, w), win_transform)

            data_before_p = _read_padded_window(ds_before, window, before_cfg)
            data_after_p  = _read_padded_window(ds_after,  window, after_cfg)
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Внутренняя ошибка чтения растровых данных")

    nodata_before_p = period_nodata_mask(data_before_p, before_cfg)
    nodata_after_p  = period_nodata_mask(data_after_p,  after_cfg)
    valid_mask = poly_mask & ~nodata_before_p[1:-1, 1:-1] & ~nodata_after_p[1:-1, 1:-1]
    pixel_count = int(np.count_nonzero(valid_mask))
    if pixel_count == 0:
        raise HTTPException(status_code=400, detail="Нет валидных пикселей внутри полигона")

    refl_before = period_to_reflectance(data_before_p, before_cfg)[:, 1:-1, 1:-1]
    refl_after  = period_to_reflectance(data_after_p,  after_cfg)[:, 1:-1, 1:-1]
    idx_before  = _all_indices_from_refl(refl_before)
    idx_after   = _all_indices_from_refl(refl_after)

    indices_out = {
        key: _change_index_stats_from_arrays(key, idx_before[key][valid_mask], idx_after[key][valid_mask])
        for key in _CHANGE_INDEX_KEYS
    }

    px_area_ha = 0.01
    ml_transitions = None
    if CLASSIFIER_V2 is not None and LABEL_ENCODER_V2 is not None:
        texture_before_p = period_to_texture_reflectance(data_before_p, before_cfg)
        texture_after_p  = period_to_texture_reflectance(data_after_p,  after_cfg)
        stds_before = _v3_texture_stds(texture_before_p, ~nodata_before_p)
        stds_after  = _v3_texture_stds(texture_after_p,  ~nodata_after_p)

        pred_before = _predict_v3_encoded(idx_before, refl_before, stds_before, valid_mask)
        pred_after  = _predict_v3_encoded(idx_after,  refl_after,  stds_after,  valid_mask)

        n_classes = len(CLASS_NAMES_V2)
        pair_idx = pred_before * n_classes + pred_after
        counts = np.bincount(pair_idx, minlength=n_classes * n_classes).reshape(n_classes, n_classes)
        ml_transitions = _transitions_from_counts(counts, pixel_count, px_area_ha)

    return {
        "area_ha":       round(pixel_count * px_area_ha, 2),
        "pixel_count":   pixel_count,
        "period_before": period_before,
        "period_after":  period_after,
        "indices":       indices_out,
        "ml_transitions": ml_transitions,
        "evidence": {
            **period_evidence(
                period_after,
                after_cfg,
                quality={
                    "valid_pixel_count": pixel_count,
                    "comparison_mask": "valid_in_both_periods",
                },
            ),
            "kind": "derived_change_observation",
            "acquisition_window": f"{before_cfg['date_range']} → {after_cfg['date_range']}",
            "data_version": f"{period_data_version(before_cfg)}.{period_data_version(after_cfg)}",
            "input_periods": [
                {"period_id": period_before, "data_version": period_data_version(before_cfg)},
                {"period_id": period_after, "data_version": period_data_version(after_cfg)},
            ],
        },
    }


@app.post("/api/change_stats")
@limit_analysis
def change_stats(req: ChangeStatsReq):
    stats = _compute_change_stats(req.geometry, req.period_before, req.period_after)

    groq_analysis = None
    if AI_OK:
        key = os.getenv("GROQ_API_KEY")
        if key:
            try:
                prompt = build_change_stats_prompt(stats, req.locale)
                response_language = AI_LANGUAGE_NAMES.get(req.locale, AI_LANGUAGE_NAMES["ru"])
                system = ("Эксперт по дистанционному зондированию и агрономии Центральной Азии. "
                          f"Анализируешь изменения между двумя периодами Sentinel-2. Отвечай кратко и конкретно на {response_language} языке.")
                r = ai_client(key, "https://api.groq.com/openai/v1").chat.completions.create(
                    model="llama-3.1-8b-instant", max_tokens=500, temperature=0.4,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": prompt}])
                groq_analysis = r.choices[0].message.content
            except Exception as e:
                print(f"Change stats AI error: {e}")

    stats["groq_analysis"] = groq_analysis
    return stats


# ════════════════════════════════════════════════════════════════
#   CHANGE OVERVIEW — whole-oblast precompute, background thread
#
#   The boundary bbox is ~40,700 x 60,200 px at 10m — reading both periods'
#   7 bands at once there would need ~137GB RAM. So this walks the bbox in
#   2048x2048 blocks and accumulates running sums/counts/transition tallies
#   instead of holding the whole area in memory. Two passes over the blocks:
#   pass 1 gets sum/sum-of-squares per index (-> global mean & std_delta) and
#   the ML transition matrix; pass 2 (needs the now-known global std_delta)
#   counts how many pixels clear the 1.5*std_delta significance bar. This
#   doubles the COG I/O for this one-time startup job, traded deliberately
#   for a single region-wide significance threshold instead of a per-block
#   one (a per-block threshold would make "significant_pct" mean a different
#   thing in a flat block vs. a noisy one).
# ════════════════════════════════════════════════════════════════

_OVERVIEW_BLOCK_SIZE = 2048
_OVERVIEW_LOCK = threading.Lock()
_OVERVIEW_CACHE: dict = {"status": "computing", "progress": 0}
BOUNDARY_PATH = BASE_DIR / "frontend" / "public" / "turkestan_boundary.geojson"


def _load_boundary_geometry() -> dict:
    with open(BOUNDARY_PATH, encoding="utf-8") as f:
        gj = json.load(f)
    return gj["features"][0]["geometry"]


def _finalize_index_stats_streaming(index: str, agg: dict) -> dict:
    count = agg["count"]
    mean_before = agg["sum_before"] / count
    mean_after  = agg["sum_after"]  / count
    mean_delta  = agg["sum_delta"]  / count
    var_delta   = agg["sum_delta_sq"] / count - mean_delta ** 2
    std_delta   = float(np.sqrt(max(var_delta, 0)))
    significant_pct = (agg["sig_count"] / count * 100) if count else 0.0
    return _finalize_direction(index, mean_before, mean_after, mean_delta, std_delta, significant_pct)


def _set_overview_progress(pct: float):
    with _OVERVIEW_LOCK:
        _OVERVIEW_CACHE["progress"] = round(pct)


def _compute_change_overview(period_before: str = "2023_summer", period_after: str = "2025_summer") -> dict:
    load_classifier_v2()
    before_cfg = resolve_period(period_before)
    after_cfg  = resolve_period(period_after)
    polygons = _normalize_geometry_polygons(_load_boundary_geometry())

    with rasterio.open(before_cfg["cog_path"]) as ds_before, rasterio.open(after_cfg["cog_path"]) as ds_after:
        transformer = Transformer.from_crs("EPSG:4326", ds_before.crs, always_xy=True)
        polygons_proj = _project_polygons(polygons, transformer)
        minx, miny, maxx, maxy = _polygons_bounds(polygons_proj)

        ds_l, ds_b, ds_r, ds_t = ds_before.bounds
        minx, maxx = max(minx, ds_l), min(maxx, ds_r)
        miny, maxy = max(miny, ds_b), min(maxy, ds_t)

        full_window = window_from_bounds(minx, miny, maxx, maxy, transform=ds_before.transform)
        full_window = full_window.round_offsets().round_lengths()
        col0, row0 = int(full_window.col_off), int(full_window.row_off)
        total_w, total_h = int(full_window.width), int(full_window.height)

        bs = _OVERVIEW_BLOCK_SIZE
        blocks = [
            Window(col0 + bc, row0 + br, min(bs, total_w - bc), min(bs, total_h - br))
            for br in range(0, total_h, bs)
            for bc in range(0, total_w, bs)
        ]
        n_blocks = len(blocks)

        n_classes = len(CLASS_NAMES_V2) if CLASS_NAMES_V2 else 0
        transition_counts = np.zeros((n_classes, n_classes), dtype=np.int64) if n_classes else None
        agg = {k: {"sum_before": 0.0, "sum_after": 0.0, "sum_delta": 0.0, "sum_delta_sq": 0.0,
                   "count": 0, "sig_count": 0} for k in _CHANGE_INDEX_KEYS}

        # ── Pass 1: sums/sumsq per index + ML transition matrix ──
        for n, blk in enumerate(blocks):
            win_transform = ds_before.window_transform(blk)
            h, w = int(blk.height), int(blk.width)
            poly_mask = _polygons_geometry_mask(polygons_proj, (h, w), win_transform)
            if not poly_mask.any():
                _set_overview_progress((n + 1) / n_blocks * 50)
                continue

            data_before_p = _read_padded_window(ds_before, blk, before_cfg)
            data_after_p  = _read_padded_window(ds_after,  blk, after_cfg)
            nodata_before_p = period_nodata_mask(data_before_p, before_cfg)
            nodata_after_p  = period_nodata_mask(data_after_p,  after_cfg)
            valid_mask = poly_mask & ~nodata_before_p[1:-1, 1:-1] & ~nodata_after_p[1:-1, 1:-1]
            if not valid_mask.any():
                _set_overview_progress((n + 1) / n_blocks * 50)
                continue

            refl_before = period_to_reflectance(data_before_p, before_cfg)[:, 1:-1, 1:-1]
            refl_after  = period_to_reflectance(data_after_p,  after_cfg)[:, 1:-1, 1:-1]
            idx_before  = _all_indices_from_refl(refl_before)
            idx_after   = _all_indices_from_refl(refl_after)

            n_valid = int(valid_mask.sum())
            for key in _CHANGE_INDEX_KEYS:
                bv = idx_before[key][valid_mask]
                av = idx_after[key][valid_mask]
                d = av - bv
                a = agg[key]
                a["sum_before"]   += float(bv.sum())
                a["sum_after"]    += float(av.sum())
                a["sum_delta"]    += float(d.sum())
                a["sum_delta_sq"] += float(np.square(d).sum())
                a["count"]        += n_valid

            if transition_counts is not None:
                texture_before_p = period_to_texture_reflectance(data_before_p, before_cfg)
                texture_after_p  = period_to_texture_reflectance(data_after_p,  after_cfg)
                stds_before = _v3_texture_stds(texture_before_p, ~nodata_before_p)
                stds_after  = _v3_texture_stds(texture_after_p,  ~nodata_after_p)
                pred_before = _predict_v3_encoded(idx_before, refl_before, stds_before, valid_mask)
                pred_after  = _predict_v3_encoded(idx_after,  refl_after,  stds_after,  valid_mask)
                pair_idx = pred_before * n_classes + pred_after
                transition_counts += np.bincount(pair_idx, minlength=n_classes * n_classes).reshape(n_classes, n_classes)

            _set_overview_progress((n + 1) / n_blocks * 50)

        std_delta_global = {}
        for key in _CHANGE_INDEX_KEYS:
            a = agg[key]
            if a["count"]:
                mean_d = a["sum_delta"] / a["count"]
                var_d  = a["sum_delta_sq"] / a["count"] - mean_d ** 2
                std_delta_global[key] = float(np.sqrt(max(var_d, 0)))
            else:
                std_delta_global[key] = 0.0

        # ── Pass 2: count pixels significant against the now-known global std_delta ──
        for n, blk in enumerate(blocks):
            win_transform = ds_before.window_transform(blk)
            h, w = int(blk.height), int(blk.width)
            poly_mask = _polygons_geometry_mask(polygons_proj, (h, w), win_transform)
            if not poly_mask.any():
                _set_overview_progress(50 + (n + 1) / n_blocks * 50)
                continue

            data_before_p = _read_padded_window(ds_before, blk, before_cfg)
            data_after_p  = _read_padded_window(ds_after,  blk, after_cfg)
            nodata_before_p = period_nodata_mask(data_before_p, before_cfg)
            nodata_after_p  = period_nodata_mask(data_after_p,  after_cfg)
            valid_mask = poly_mask & ~nodata_before_p[1:-1, 1:-1] & ~nodata_after_p[1:-1, 1:-1]
            if not valid_mask.any():
                _set_overview_progress(50 + (n + 1) / n_blocks * 50)
                continue

            refl_before = period_to_reflectance(data_before_p, before_cfg)[:, 1:-1, 1:-1]
            refl_after  = period_to_reflectance(data_after_p,  after_cfg)[:, 1:-1, 1:-1]
            idx_before  = _all_indices_from_refl(refl_before)
            idx_after   = _all_indices_from_refl(refl_after)

            for key in _CHANGE_INDEX_KEYS:
                d = idx_after[key][valid_mask] - idx_before[key][valid_mask]
                thresh = 1.5 * std_delta_global[key]
                if thresh > 0:
                    agg[key]["sig_count"] += int(np.count_nonzero(np.abs(d) > thresh))

            _set_overview_progress(50 + (n + 1) / n_blocks * 50)

    indices_out = {key: _finalize_index_stats_streaming(key, agg[key]) for key in _CHANGE_INDEX_KEYS}
    pixel_count = agg[_CHANGE_INDEX_KEYS[0]]["count"]
    px_area_ha = 0.01
    ml_transitions = _transitions_from_counts(transition_counts, pixel_count, px_area_ha) if transition_counts is not None else None

    result = {
        "status":        "ready",
        "progress":      100,
        "area_ha":       round(pixel_count * px_area_ha, 2),
        "pixel_count":   pixel_count,
        "period_before": period_before,
        "period_after":  period_after,
        "indices":       indices_out,
        "ml_transitions": ml_transitions,
    }

    groq_analysis = None
    if AI_OK:
        key_env = os.getenv("GROQ_API_KEY")
        if key_env:
            try:
                prompt = build_change_stats_prompt(result)
                system = ("Эксперт по дистанционному зондированию и агрономии Центральной Азии. "
                          "Анализируешь изменения по всей Туркестанской области между двумя периодами Sentinel-2.")
                r = ai_client(key_env, "https://api.groq.com/openai/v1").chat.completions.create(
                    model="llama-3.1-8b-instant", max_tokens=600, temperature=0.4,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": prompt}])
                groq_analysis = r.choices[0].message.content
            except Exception as e:
                print(f"Change overview AI error: {e}")
    result["groq_analysis"] = groq_analysis

    return result


def _run_overview_background():
    try:
        result = _compute_change_overview()
        with _OVERVIEW_LOCK:
            _OVERVIEW_CACHE.clear()
            _OVERVIEW_CACHE.update(result)
    except Exception as e:
        traceback.print_exc()
        with _OVERVIEW_LOCK:
            _OVERVIEW_CACHE.clear()
            _OVERVIEW_CACHE.update({"status": "error", "progress": 0, "detail": str(e)})


if ENABLE_CHANGE_OVERVIEW and RASTERIO_OK and BOUNDARY_PATH.exists():
    threading.Thread(target=_run_overview_background, daemon=True).start()
elif not ENABLE_CHANGE_OVERVIEW:
    _OVERVIEW_CACHE = {
        "status": "disabled",
        "progress": 0,
        "detail": "Фоновый расчёт отключён; установите ENABLE_CHANGE_OVERVIEW=true для запуска",
    }
else:
    _OVERVIEW_CACHE = {"status": "error", "progress": 0, "detail": "COG/граница области недоступны на сервере"}


@app.get("/api/change_overview")
async def change_overview():
    with _OVERVIEW_LOCK:
        return dict(_OVERVIEW_CACHE)


# ════════════════════════════════════════════════════════════════
#   METADATA
# ════════════════════════════════════════════════════════════════

@app.get("/metadata")
async def metadata():
    bounds = cog_bounds_wgs84() or REGION_FALLBACK_BOUNDS
    center = [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2]
    return {
        "region": {
            "name":    "Туркестанская область, Казахстан",
            "bounds":  bounds,
            "center":  center,
        },
        "layers": LAYERS,
        "cmaps":  CMAP_CSS,
        "cog":      cog_available(),
        "s2_tiles": len(s2_assets()),
        "source": "Sentinel-2 L2A / Copernicus Data Space Ecosystem",
        "evidence": period_evidence(DEFAULT_PERIOD, PERIODS[DEFAULT_PERIOD]),
        "imagery": {
            "true_color": {"available": cog_available(), "bands": ["B04", "B03", "B02"], "stretch": "2.5x reflectance + gamma 2.2"},
        },
        "timelapse": CDSE_SCENE_CATALOG.capabilities(),
        "forecast": forecast_config(),
    }


# Register CORS after the function-based middleware so it becomes the
# outermost layer. Early 413/429 responses must expose their real HTTP status
# and retry headers to the browser instead of being hidden as a CORS failure.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)


# ── Static data files ─────────────────────────────────────────────
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "true").lower() in {"1", "true", "yes"},
    )
