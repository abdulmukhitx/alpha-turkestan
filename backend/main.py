"""
GeoAI-TKO — FastAPI Backend v4
================================
Primary data source: merged COG mosaic (S2_COG_PATH, default D:\\data\\s2_mosaic_cog.tif)
  7 bands: B02=1 B03=2 B04=3 B05=4 B08=5 B8A=6 B11=7   CRS EPSG:32641, 10m
Fallback: src/processing/s2_work/*.tif (per-scene tiles, merged on the fly)

Endpoints:
  GET  /health                          service check
  GET  /tiles/{layer}/{z}/{x}/{y}.png   XYZ tiles  (rgb / ndvi / ndwi / ndre / ndmi / bsi)
  GET  /api/pixel?lat=&lon=             per-pixel spectral values + indices
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

# ── Paths ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR  = BASE_DIR / "data"
S2_DIR    = BASE_DIR / "src" / "processing" / "s2_work"
COG_PATH  = Path(os.getenv("S2_COG_PATH", r"D:\data\s2_mosaic_cog.tif"))

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


# ── Band indices (0-based) inside the 8-band S2 TIF ──────────────
# B02=0 B03=1 B04=2 B05=3 B08=4 B8A=5 B11=6 SCL=7
_B02, _B03, _B04, _B05, _B08, _B8A, _B11 = 0, 1, 2, 3, 4, 5, 6


# ── Layer config ──────────────────────────────────────────────────
LAYERS = {
    "rgb":  {"label": "RGB снимок",            "range": None,          "cmap": None},
    "ndvi": {"label": "NDVI — растительность", "range": (-0.2,  0.8), "cmap": "rdylgn"},
    "ndwi": {"label": "NDWI — водные объекты", "range": (-0.5,  0.6), "cmap": "blues"},
    "ndre": {"label": "NDRE — стресс растений","range": (-0.2,  0.6), "cmap": "greens"},
    "ndmi": {"label": "NDMI — влажность почвы","range": (-0.5,  0.5), "cmap": "rdbu"},
    "bsi":  {"label": "BSI — голая почва",     "range": (-0.5,  0.5), "cmap": "oranges"},
}

CMAP_CSS = {
    "rgb":  None,
    "ndvi": "linear-gradient(to right,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)",
    "ndwi": "linear-gradient(to right,#f7fbff,#74a9cf,#0570b0,#023858)",
    "ndre": "linear-gradient(to right,#f7fcf5,#c7e9c0,#41ab5d,#00441b)",
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

    rio-tiler mask convention: 255 = valid pixel, 0 = nodata.
    """
    if cog_available():
        try:
            with Reader(str(COG_PATH)) as src:
                img = src.tile(x, y, z, tilesize=256)
            return img.data.astype(np.float32), img.mask
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

        d = img.data.astype(np.float32)   # (bands, 256, 256)
        m = img.mask                       # (256, 256)  255=valid

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
def render_rgb(data: "np.ndarray", mask: "np.ndarray") -> bytes:
    # Stretch: 0–2500 DN → 0–255 uint8 (good for S2 SR)
    r = np.clip(data[_B04] / 2500 * 255, 0, 255).astype(np.uint8)
    g = np.clip(data[_B03] / 2500 * 255, 0, 255).astype(np.uint8)
    b = np.clip(data[_B02] / 2500 * 255, 0, 255).astype(np.uint8)
    rgba = np.stack([r, g, b, mask], axis=-1)
    buf  = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


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
        if layer == "rgb":
            content = render_rgb(data, mask)
        else:
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

def _classify(ndvi, ndwi):
    if ndvi is None:          return "Нет данных",            "—"
    if ndwi and ndwi > 0.2:  return "Водная поверхность",    "→ стабильно"
    if ndvi > 0.50:           return "Густая растительность", "↑ активный рост"
    if ndvi > 0.30:           return "Ирригированное поле",   "↑ хорошее состояние"
    if ndvi > 0.15:           return "Пастбище",              "→ умеренное"
    if ndvi > 0.05:           return "Деградирующие земли",   "↓ требует мониторинга"
    return "Голая почва / пустыня", "→ минимальная активность"

def _demo(lat, lon):
    import math
    s = abs(math.sin(lat * 100) * math.cos(lon * 100))
    b = {f"B{n}": round(0.05 + s * 0.3 + i * 0.02, 3)
         for i, n in enumerate(["02","03","04","05","08","8A","11"])}
    eps = 1e-10
    b02,b03,b04 = b["B02"],b["B03"],b["B04"]
    b05,b08,b8a,b11 = b["B05"],b["B08"],b["B8A"],b["B11"]
    return {
        "ndvi": round((b08-b04)/(b08+b04+eps),3),
        "ndwi": round((b03-b08)/(b03+b08+eps),3),
        "ndre": round((b08-b05)/(b08+b05+eps),3),
        "ndmi": round((b8a-b11)/(b8a+b11+eps),3),
        "bsi":  round(((b11+b04)-(b08+b02))/((b11+b04)+(b08+b02)+eps),3),
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
            return {
                "ndvi": round((b[4]-b[2])/(b[4]+b[2]+eps),3),
                "ndwi": round((b[1]-b[4])/(b[1]+b[4]+eps),3),
                "ndre": round((b[4]-b[3])/(b[4]+b[3]+eps),3),
                "ndmi": round((b[5]-b[6])/(b[5]+b[6]+eps),3),
                "bsi":  round(((b[6]+b[2])-(b[4]+b[0]))/((b[6]+b[2])+(b[4]+b[0])+eps),3),
                "bands": {
                    "B02":round(b[0],4),"B03":round(b[1],4),"B04":round(b[2],4),
                    "B05":round(b[3],4),"B08":round(b[4],4),"B8A":round(b[5],4),
                    "B11":round(b[6],4),
                },
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

    land, trend = _classify(safe(result.get("ndvi")), safe(result.get("ndwi")))
    return {
        "lat": lat, "lon": lon,
        "ndvi": safe(result.get("ndvi")),
        "ndwi": safe(result.get("ndwi")),
        "ndre": safe(result.get("ndre")),
        "ndmi": safe(result.get("ndmi")),
        "bsi":  safe(result.get("bsi")),
        "bands":      result.get("bands", {}),
        "land_class": land,
        "trend_label":trend,
        "demo":       result.get("demo", False),
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
    land_class: str   | None = None


def _surface_hint(ndvi, ndwi, bsi) -> str:
    """Coarse land-cover guess to anchor the AI prompt with correct context."""
    if ndwi is not None and ndwi > 0.2:   return "водный объект (река, озеро или канал)"
    if ndvi is not None and ndvi > 0.40:  return "густая растительность (посевы или природная)"
    if ndvi is not None and ndvi > 0.15:  return "разреженная растительность / сельхозугодья"
    if bsi  is not None and bsi  > 0.10:  return "голая почва или деградированные земли"
    return "смешанная / переходная зона"


@app.post("/api/analyze")
async def analyze(req: AnalyzeReq):
    hint = _surface_hint(req.ndvi, req.ndwi, req.bsi)
    idx_lines = []
    if req.ndvi is not None: idx_lines.append(f"NDVI (растительность)={req.ndvi:.3f}")
    if req.ndwi is not None: idx_lines.append(f"NDWI (вода)={req.ndwi:.3f}")
    if req.ndre is not None: idx_lines.append(f"NDRE (стресс растений)={req.ndre:.3f}")
    if req.ndmi is not None: idx_lines.append(f"NDMI (влажность)={req.ndmi:.3f}")
    if req.bsi  is not None: idx_lines.append(f"BSI (голая почва)={req.bsi:.3f}")

    prompt = (
        f"Точка {req.lat:.4f}°N, {req.lon:.4f}°E — Туркестанская область Казахстана.\n"
        f"Предполагаемый тип поверхности: {hint}.\n"
        + ("Спектральные индексы: " + "; ".join(idx_lines) + ".\n" if idx_lines else "")
        + (f"Класс: {req.land_class}.\n" if req.land_class else "")
        + "Дай краткую экспертную интерпретацию (2–3 предложения) на русском: "
          "тип землепользования, экологическое состояние и сельскохозяйственное значение."
    )
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
#   METADATA
# ════════════════════════════════════════════════════════════════

@app.get("/metadata")
async def metadata():
    bounds = cog_bounds_wgs84() or [40.8, 67.5, 44.0, 71.5]  # S,W,N,E
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
