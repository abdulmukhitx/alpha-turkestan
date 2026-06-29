"""
GeoAI-TKO — FastAPI Backend v4
================================
Primary data source: merged COG mosaic (S2_COG_PATH, default D:\\data\\s2_mosaic_cog.tif)
  7 bands: B02=1 B03=2 B04=3 B05=4 B08=5 B8A=6 B11=7   CRS EPSG:32641, 10m
Fallback: src/processing/s2_work/*.tif (per-scene tiles, merged on the fly)

Endpoints:
  GET  /health                          service check
  GET  /tiles/{layer}/{z}/{x}/{y}.png   XYZ tiles  (ndvi / ndwi / ndre / ndmi / bsi)
  GET  /api/pixel?lat=&lon=             per-pixel spectral values + indices
  POST /api/zone_stats                  zonal stats (indices + LULC) for a drawn polygon
  POST /api/zone_report                 structured Groq report for a drawn zone (PDF built on frontend)
  POST /api/analyze                     AI interpretation (Groq / DeepSeek / local fallback)
  GET  /metadata                        region + layer metadata
  GET  /data/...                        static data files

Run:
  uvicorn backend.main:app --reload --port 8000
"""

import io, os, traceback
from pathlib import Path

from dotenv import load_dotenv
# Load project-root .env explicitly so it works regardless of CWD
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, Query, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ── Optional geo libs ─────────────────────────────────────────────
try:
    from rio_tiler.io import Reader
    from rio_tiler.errors import TileOutsideBounds
    import numpy as np
    from PIL import Image
    TILER_OK = True
except ImportError as e:
    TILER_OK = False
    print(f"⚠  rio-tiler / Pillow not available: {e}")

try:
    import rasterio
    from rasterio.windows import from_bounds as window_from_bounds
    from rasterio.features import geometry_mask
    from pyproj import Transformer
    RASTERIO_OK = True
except ImportError:
    RASTERIO_OK = False
    print("⚠  rasterio not available — pixel endpoint uses demo data")

try:
    from openai import OpenAI
    AI_OK = True
except ImportError:
    AI_OK = False

try:
    import pickle
    import sklearn  # noqa: F401  needed so pickle.load can resolve the saved estimator
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False
    print("⚠  scikit-learn not available — ML land-cover classification disabled")

# ── Paths ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR  = BASE_DIR / "data"
S2_DIR    = BASE_DIR / "src" / "processing" / "s2_work"
COG_PATH  = Path(os.getenv("S2_COG_PATH", r"D:\data\s2_mosaic_cog.tif"))
LULC_MODEL_PATH = Path(os.getenv("LULC_MODEL_PATH", r"D:\data\lulc_classifier.pkl"))
LULC_MODEL_V2_PATH = Path(os.getenv("LULC_MODEL_V2_PATH", r"D:\data\lulc_classifier_v2.pkl"))

def s2_assets() -> list[str]:
    """Sorted list of all S2 GeoTIFF paths (fallback source)."""
    return sorted(str(p) for p in S2_DIR.glob("*.tif")) if S2_DIR.exists() else []

def cog_available() -> bool:
    return RASTERIO_OK and COG_PATH.exists()

_COG_BOUNDS_WGS84: tuple | None = None

def cog_bounds_wgs84() -> list[float] | None:
    """[south, west, north, east] of the COG, reprojected to WGS84. Cached."""
    global _COG_BOUNDS_WGS84
    if not cog_available():
        return None
    if _COG_BOUNDS_WGS84 is None:
        with rasterio.open(COG_PATH) as ds:
            from rasterio.warp import transform_bounds
            l, b, r, t = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
            _COG_BOUNDS_WGS84 = (b, l, t, r)  # S, W, N, E
    return list(_COG_BOUNDS_WGS84)


# ── ML land-cover classifier ─────────────────────────────────────
# v1: RandomForest (6 features: NDVI/NDRE/NDWI/NDMI/BSI/B08), trained by
#     src/processing/train_lulc_rf.py (legacy). Kept loaded and in active use
#     by /api/zone_stats's bulk per-pixel classification, which builds the
#     6-feature array — swapping the global model there would break it, so
#     v1 and v2 are deliberately kept as separate models/globals.
# v2: XGBoost (13 features: v1's 6 + 7 std_* 3x3-texture features), trained
#     by src/processing/extract_samples.py + train_xgb.py. Used only by
#     /api/pixel via classify_ml_v2(). CV accuracy 88.9% vs v1's OOB 74.25%.
# Both optional: if a pickle isn't there yet, the relevant ml_* fields are
# just omitted and everything else keeps working.
CLASSIFIER  = None
CLASS_NAMES: "list[str] | None" = None

CLASSIFIER_V2 = None
LABEL_ENCODER_V2 = None
CLASS_NAMES_V2: "list[str] | None" = None

_ML_CLASS_RU = {
    "water":              "Вода",
    "dense_vegetation":   "Густая растительность",
    "agriculture":        "Сельхозугодья",
    "sparse_vegetation":  "Разреженная растительность",
    "bare_soil":          "Голая почва",
    "urban":              "Застройка",
}

def load_classifier():
    global CLASSIFIER, CLASS_NAMES
    if not (SKLEARN_OK and LULC_MODEL_PATH.exists()):
        return
    try:
        with open(LULC_MODEL_PATH, "rb") as f:
            saved = pickle.load(f)
        CLASSIFIER  = saved["model"]
        CLASS_NAMES = list(saved["label_encoder"].classes_)
        print(f"✓ LULC classifier loaded: {len(CLASS_NAMES)} classes")
    except Exception as e:
        print(f"⚠  failed to load LULC classifier: {e}")

load_classifier()


def load_classifier_v2():
    global CLASSIFIER_V2, LABEL_ENCODER_V2, CLASS_NAMES_V2
    if not LULC_MODEL_V2_PATH.exists():
        return
    try:
        with open(LULC_MODEL_V2_PATH, "rb") as f:
            saved = pickle.load(f)
        CLASSIFIER_V2    = saved["model"]
        LABEL_ENCODER_V2 = saved["label_encoder"]
        CLASS_NAMES_V2   = list(saved["classes"])
        print(f"✓ LULC classifier v2 (XGBoost) loaded: {len(CLASS_NAMES_V2)} classes")
    except Exception as e:
        print(f"⚠  failed to load LULC classifier v2: {e}")

load_classifier_v2()


def classify_ml(ndvi, ndre, ndwi, ndmi, bsi, b08):
    """RandomForest land-cover prediction from the 5 indices + B08. None if unavailable.
    Legacy v1 — kept for /api/zone_stats's bulk classification (6 features)."""
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
    features (3x3 window std per band, raw DN). Used by /api/pixel only."""
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
    "ndre": {"label": "NDRE — стресс растений","range": (-0.30, 0.00), "cmap": "rdylgn"},
    "ndmi": {"label": "NDMI — влажность почвы","range": (-0.20, 0.16), "cmap": "rdbu"},
    "bsi":  {"label": "BSI — голая почва",     "range": (0.12,  0.29), "cmap": "oranges"},
}

CMAP_CSS = {
    "ndvi": "linear-gradient(to right,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)",
    "ndwi": "linear-gradient(to right,#b2182b,#f7f7f7,#2166ac)",   # rdbu: dry→water
    "ndre": "linear-gradient(to right,#d73027,#fee08b,#1a9850)",   # rdylgn: stress→healthy
    "ndmi": "linear-gradient(to right,#67001f,#f4a582,#f7f7f7,#92c5de,#053061)",
    "bsi":  "linear-gradient(to right,#fff5eb,#fdd0a2,#fd8d3c,#d94801,#7f2704)",
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
def mosaic_tile(x: int, y: int, z: int):
    """
    Read one 256×256 tile, preferring the merged COG mosaic.
    Returns (data: float32 array (bands, 256, 256), mask: uint8 (256, 256))
    or (None, None) when no coverage.

    Mask is derived from the raw bands ourselves — valid unless ALL bands == 0 —
    rather than trusting rio-tiler's img.mask. rio-tiler flags a pixel nodata as
    soon as ANY single band reads 0, which is too aggressive here: it kills real
    pixels where one band legitimately reads ~0 (and the computed index is ≈0)
    while the rest of the bands, and the COG's actual nodata convention (all 7
    bands == 0), say the pixel is valid.
    """
    if cog_available():
        try:
            with Reader(str(COG_PATH)) as src:
                img = src.tile(x, y, z, tilesize=256)
            data = img.data.astype(np.float32)
            mask = (~np.all(data == 0, axis=0)).astype(np.uint8) * 255
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
def compute_index(data: "np.ndarray", layer: str) -> "np.ndarray":
    """
    data: float32 (bands, 256, 256) — raw DN ~0..10 000
    Returns float32 (256, 256) index values.
    """
    eps = 1e-10
    # Convert to surface reflectance (0..1)
    b02 = data[_B02] / 10000
    b03 = data[_B03] / 10000
    b04 = data[_B04] / 10000
    b05 = data[_B05] / 10000
    b08 = data[_B08] / 10000
    b8a = data[_B8A] / 10000
    b11 = data[_B11] / 10000

    if   layer == "ndvi": return (b08 - b04) / (b08 + b04 + eps)
    elif layer == "ndwi": return (b03 - b08) / (b03 + b08 + eps)
    elif layer == "ndre": return (b08 - b05) / (b08 + b05 + eps)
    elif layer == "ndmi": return (b8a - b11) / (b8a + b11 + eps)
    elif layer == "bsi":
        num = (b11 + b04) - (b08 + b02)
        den = (b11 + b04) + (b08 + b02)
        return num / (den + eps)
    return np.zeros(data.shape[1:], dtype=np.float32)


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


# ════════════════════════════════════════════════════════════════
#   APP
# ════════════════════════════════════════════════════════════════

app = FastAPI(
    title      = "GeoAI-TKO API",
    version    = "3.0.0",
    docs_url   = "/api/docs",
    redoc_url  = "/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["GET", "POST", "OPTIONS"],
    allow_headers  = ["*"],
)


@app.get("/health")
async def health():
    assets = s2_assets()
    return {
        "status":    "ok",
        "version":   "4.0.0",
        "tiler":     TILER_OK,
        "rasterio":  RASTERIO_OK,
        "ai":        AI_OK,
        "cog":       cog_available(),
        "cog_path":  str(COG_PATH),
        "s2_tiles":  len(assets),
        "s2_dir":    str(S2_DIR),
        "ai_ready":  bool(os.getenv("GROQ_API_KEY") or os.getenv("DEEPSEEK_API_KEY")),
        "lulc_classifier": CLASSIFIER is not None,
        "lulc_classifier_v2": CLASSIFIER_V2 is not None,
    }


# ════════════════════════════════════════════════════════════════
#   TILE ENDPOINT
# ════════════════════════════════════════════════════════════════

@app.get("/tiles/{layer}/{z}/{x}/{y}.png")
async def tile(layer: str, z: int, x: int, y: int):
    if not TILER_OK or layer not in LAYERS:
        return Response(content=blank_tile(), media_type="image/png")

    data, mask = mosaic_tile(x, y, z)

    if data is None:
        return Response(content=blank_tile(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=60"})

    # Normalize mask to uint8 0/255. rio-tiler 9.x returns a uint16 mask
    # (valid = 65535); stacking that with uint8 RGB would upcast the RGBA
    # array to uint16 and make PIL's "RGBA" fromarray throw → blank tiles.
    mask = (mask > 0).astype(np.uint8) * 255

    try:
        cfg     = LAYERS[layer]
        vmin, vmax = cfg["range"]
        index   = compute_index(data, layer)
        content = render_index(index, mask, cfg["cmap"], vmin, vmax)
    except Exception as e:
        print(f"render error {layer}/{z}/{x}/{y}: {e}")
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
        "bands": b, "demo": True,
    }


@app.get("/api/pixel")
async def pixel(
    # Bounds cover the full COG extent (≈40.7–46.5°N, 65.7–71.4°E) with margin.
    # The old 40.8–44.0 / 67.5–71.5 box wrongly 422'd valid points like 44.15°N.
    lat: float = Query(..., ge=40.0, le=47.0),
    lon: float = Query(..., ge=65.0, le=72.0),
):
    import math
    result = {}

    def _pixel_from(path: str):
        with rasterio.open(path) as src:
            t = Transformer.from_crs("EPSG:4326", src.crs.to_epsg(), always_xy=True)
            px, py = t.transform(lon, lat)
            row, col = src.index(px, py)
            if not (0 <= row < src.height and 0 <= col < src.width):
                return None
            raw = src.read(window=((row, row + 1), (col, col + 1))).astype(float).flatten()
            if len(raw) < 7 or not raw[:7].any():
                return None
            b   = raw[:7] / 10000  # reflectance
            eps = 1e-10

            # 3x3 window (clipped at raster edges) for the v2 classifier's
            # texture features — std per band of raw DN, same convention as
            # src/processing/extract_samples.py's read_point_window().
            row_off, col_off = max(0, row - 1), max(0, col - 1)
            row_end, col_end = min(src.height, row + 2), min(src.width, col + 2)
            win = src.read(window=((row_off, row_end), (col_off, col_end))).astype(np.float32)
            win_nodata = np.all(win == 0, axis=0)
            valid_px = ~win_nodata
            if valid_px.sum() < 3:
                std_vals = np.zeros(7, dtype=np.float32)
            else:
                std_vals = win[:, valid_px].std(axis=1)
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
                "bands": {
                    "B02":round(b[0],4),"B03":round(b[1],4),"B04":round(b[2],4),
                    "B05":round(b[3],4),"B08":round(b[4],4),"B8A":round(b[5],4),
                    "B11":round(b[6],4),
                },
                "std_bands": std_bands,
                "demo": False,
            }

    if RASTERIO_OK and cog_available():
        try:
            result = _pixel_from(str(COG_PATH)) or {}
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

    if not result:
        result = _demo(lat, lon)

    def safe(v):
        return None if (v is None or (isinstance(v,float) and math.isnan(v))) else v

    ndvi, ndre, ndwi, ndmi, bsi = (safe(result.get(k)) for k in ("ndvi","ndre","ndwi","ndmi","bsi"))
    b08 = (result.get("bands") or {}).get("B08")
    ml = classify_ml_v2(ndvi, ndre, ndwi, ndmi, bsi, b08, result.get("std_bands"))
    if ml is None:
        ml = classify_ml(ndvi, ndre, ndwi, ndmi, bsi, b08)  # fall back to v1 if v2 unavailable

    return {
        "lat": lat, "lon": lon,
        "ndvi": ndvi, "ndwi": ndwi, "ndre": ndre, "ndmi": ndmi, "bsi": bsi,
        "bands":      result.get("bands", {}),
        "ml_class":         ml["class"]         if ml else None,
        "ml_class_ru":      ml["class_ru"]      if ml else None,
        "ml_confidence":    ml["confidence"]    if ml else None,
        "ml_probabilities": ml["probabilities"] if ml else None,
        "demo":       result.get("demo", False),
    }


# ════════════════════════════════════════════════════════════════
#   ZONE STATISTICS
# ════════════════════════════════════════════════════════════════

class ZoneStatsReq(BaseModel):
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


@app.post("/api/zone_stats")
async def zone_stats(req: ZoneStatsReq):
    if not (RASTERIO_OK and cog_available()):
        raise HTTPException(status_code=500, detail="COG / rasterio недоступны на сервере")

    geom = req.geometry
    if not geom or geom.get("type") != "Polygon" or not geom.get("coordinates"):
        raise HTTPException(status_code=400, detail="Ожидается GeoJSON Polygon")

    try:
        with rasterio.open(COG_PATH) as ds:
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
        raise HTTPException(status_code=500, detail=f"Ошибка чтения COG: {e}")

    nodata_mask = np.all(data == 0, axis=0)          # all 7 bands == 0 → nodata
    valid_mask = poly_mask & ~nodata_mask
    pixel_count = int(np.count_nonzero(valid_mask))
    if pixel_count == 0:
        raise HTTPException(status_code=400, detail="Нет валидных пикселей внутри полигона")

    b02 = data[0][valid_mask] / 10000
    b03 = data[1][valid_mask] / 10000
    b04 = data[2][valid_mask] / 10000
    b05 = data[3][valid_mask] / 10000
    b08 = data[4][valid_mask] / 10000
    b8a = data[5][valid_mask] / 10000
    b11 = data[6][valid_mask] / 10000
    eps = 1e-10

    ndvi = (b08 - b04) / (b08 + b04 + eps)
    ndwi = (b03 - b08) / (b03 + b08 + eps)
    ndre = (b08 - b05) / (b08 + b05 + eps)
    ndmi = (b8a - b11) / (b8a + b11 + eps)
    bsi  = ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02) + eps)

    indices = {
        "ndvi": _zone_index_stats(ndvi),
        "ndwi": _zone_index_stats(ndwi),
        "ndre": _zone_index_stats(ndre),
        "ndmi": _zone_index_stats(ndmi),
        "bsi":  _zone_index_stats(bsi),
    }

    px_area_ha = 0.01  # 10m x 10m pixel
    lulc = {}
    if CLASSIFIER is not None:
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
        "indices":     indices,
        "lulc":        lulc,
    }


# ════════════════════════════════════════════════════════════════
#   AI ANALYSIS
# ════════════════════════════════════════════════════════════════

class AnalyzeReq(BaseModel):
    lat:        float       = Field(..., ge=40.0, le=47.0)
    lon:        float       = Field(..., ge=65.0, le=72.0)
    ndvi:       float | None = None
    ndwi:       float | None = None
    ndre:       float | None = None
    ndmi:       float | None = None
    bsi:        float | None = None
    ml_class:       str   | None = None
    ml_class_ru:    str   | None = None
    ml_confidence:  float | None = None


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


def build_groq_prompt(lat, lon, indices, ml_class, ml_class_ru, ml_confidence):
    """Builds the analysis prompt around deviations from this region's normal
    index ranges for the ML-predicted class, rather than just restating it."""
    ndvi, ndwi, ndmi, bsi, ndre = (indices.get(k) for k in ("NDVI","NDWI","NDMI","BSI","NDRE"))
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

    class_label = ml_class_ru or ml_class or "неизвестен"
    confidence_pct = round((ml_confidence or 0) * 100)

    return f"""Ты агроэколог и эксперт по землепользованию Туркестанской области Казахстана.

Данные Sentinel-2 для точки {lat:.4f}°N, {lon:.4f}°E (лето 2023):
- Тип покрова: {class_label} (уверенность {confidence_pct}%)
- NDVI={fmt(ndvi)}, NDRE={fmt(ndre)}, NDWI={fmt(ndwi)}, NDMI={fmt(ndmi)}, BSI={fmt(bsi)}
- Выявленные отклонения: {warnings_text}

Напиши 2-3 предложения на русском языке:
1. Конкретная проблема или состояние (не описывай то что уже известно из класса)
2. Практическая рекомендация для землепользователя или агронома
3. Риск если не принять меры (только если есть реальная проблема)

Будь конкретным и практичным. Не повторяй класс покрова."""


@app.post("/api/analyze")
async def analyze(req: AnalyzeReq):
    indices = {"NDVI": req.ndvi, "NDWI": req.ndwi, "NDRE": req.ndre, "NDMI": req.ndmi, "BSI": req.bsi}
    prompt = build_groq_prompt(req.lat, req.lon, indices, req.ml_class, req.ml_class_ru, req.ml_confidence)
    system = ("Эксперт по дистанционному зондированию Центральной Азии. "
              "Анализируешь Sentinel-2. Отвечай кратко и конкретно на русском.")

    if AI_OK:
        for env, url, model in [
            ("GROQ_API_KEY",    "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
            ("DEEPSEEK_API_KEY","https://api.deepseek.com",        "deepseek-chat"),
        ]:
            key = os.getenv(env)
            if not key: continue
            try:
                r = OpenAI(api_key=key, base_url=url).chat.completions.create(
                    model=model, max_tokens=280, temperature=0.3,
                    messages=[{"role":"system","content":system},
                               {"role":"user","content":prompt}])
                return {"analysis": r.choices[0].message.content,
                        "source":   env.split("_")[0].lower()}
            except Exception as e:
                print(f"AI error ({env}): {e}")

    # Local fallback
    ndvi = req.ndvi
    if ndvi is None:
        return {"analysis":"Данные недоступны — выберите другую точку.", "source":"local"}
    if   ndvi > 0.5:  txt = f"NDVI={ndvi:.2f} — активная густая растительность. Вероятно ирригированные поля или пойма Сырдарьи. Состояние хорошее."
    elif ndvi > 0.25: txt = f"NDVI={ndvi:.2f} — умеренная растительность. Характерно для пастбищ или полей в начале вегетационного сезона."
    else:             txt = f"NDVI={ndvi:.2f} — слабая растительность. Пустынные или деградированные земли. Возможны засоление и опустынивание."
    return {"analysis": txt, "source": "local"}


# ════════════════════════════════════════════════════════════════
#   ZONE REPORT (Groq) — PDF is assembled on the frontend
# ════════════════════════════════════════════════════════════════

class ZoneReportReq(BaseModel):
    geometry:         dict
    zone_stats:       dict
    active_layer:     str | None = None
    map_image_base64: str | None = None   # captured client-side, used only for the PDF


_LULC_LABELS_RU = {
    "agriculture":       "Сельхоз угодья",
    "urban":             "Застройка",
    "dense_vegetation":  "Густая растительность",
    "sparse_vegetation": "Разреженная растительность",
    "bare_soil":         "Голая почва",
    "water":             "Водные объекты",
}
_LULC_ORDER = ["agriculture", "urban", "dense_vegetation", "sparse_vegetation", "bare_soil", "water"]


def build_zone_report_prompt(stats: dict, active_layer: str | None) -> str:
    """Detailed Russian prompt for a 4-section structured zone report (vs. the
    short 2-3 sentence pixel-level prompt in build_groq_prompt above)."""
    area_ha = stats.get("area_ha") or 0
    idx     = stats.get("indices") or {}
    lulc    = stats.get("lulc") or {}

    def i(key, field):
        v = (idx.get(key) or {}).get(field)
        return f"{v:.3f}" if isinstance(v, (int, float)) else "н/д"

    lulc_lines = "\n".join(
        f"- {_LULC_LABELS_RU.get(k, k)}: {(lulc.get(k) or {}).get('area_ha', 0):.2f} га "
        f"({(lulc.get(k) or {}).get('percent', 0):.2f}%)"
        for k in _LULC_ORDER if k in lulc
    ) or "- данные классификации отсутствуют"

    return f"""Ты — эксперт по дистанционному зондированию и агрономии Казахстана. Проанализируй спутниковые данные Sentinel-2 для зоны в Туркестанской области.

ДАННЫЕ ЗОНЫ:
- Общая площадь: {area_ha:.2f} га
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

Пиши профессионально но понятно. Каждый раздел 3-4 предложения.
Заголовок каждого раздела пиши ровно в формате "N. НАЗВАНИЕ" заглавными буквами на отдельной строке, без markdown-разметки (без звёздочек, решёток и слова "Раздел")."""


def _local_zone_report(stats: dict) -> str:
    """Template fallback used only if Groq is unreachable/unconfigured."""
    area_ha = stats.get("area_ha") or 0
    idx     = stats.get("indices") or {}
    lulc    = stats.get("lulc") or {}
    ndvi    = (idx.get("ndvi") or {}).get("mean")
    top_class = max(lulc.items(), key=lambda kv: kv[1].get("area_ha", 0))[0] if lulc else None
    top_label = _LULC_LABELS_RU.get(top_class, top_class or "неизвестно")
    ndvi_text = f"{ndvi:.3f}" if ndvi is not None else "н/д"

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
async def zone_report(req: ZoneReportReq):
    prompt = build_zone_report_prompt(req.zone_stats, req.active_layer)
    system = ("Эксперт по дистанционному зондированию и агрономии Центральной Азии. "
              "Пишешь развёрнутые структурированные аналитические отчёты на русском языке.")

    if AI_OK:
        key = os.getenv("GROQ_API_KEY")
        if key:
            try:
                # llama3-8b-8192 was decommissioned by Groq — llama-3.1-8b-instant is
                # its direct successor in the same fast/cheap 8B tier.
                r = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1").chat.completions.create(
                    model="llama-3.1-8b-instant", max_tokens=1500, temperature=0.4,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": prompt}])
                return {"groq_analysis": r.choices[0].message.content}
            except Exception as e:
                print(f"Zone report AI error: {e}")

    return {"groq_analysis": _local_zone_report(req.zone_stats)}


# ════════════════════════════════════════════════════════════════
#   METADATA
# ════════════════════════════════════════════════════════════════

@app.get("/metadata")
async def metadata():
    bounds = cog_bounds_wgs84() or [40.31, 65.36, 46.46, 71.36]  # S,W,N,E
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
        "source": "Sentinel-2 SR / Google Earth Engine",
    }


# ── Static data files ─────────────────────────────────────────────
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
