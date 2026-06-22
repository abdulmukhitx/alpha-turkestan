"""
GeoAI-TKO · src/api/main.py

Запуск: uvicorn src.api.main:app --reload --port 8000

Структура:
  GET  /health              — проверка сервиса
  GET  /cog/tiles/{layer}/{year}/{z}/{x}/{y}.png — динамические XYZ-тайлы
  GET  /cog/info            — метаданные COG
  GET  /rasters/{layer}     — PNG растра (совместимость)
  GET  /api/pixel           — значения индексов для точки
  POST /api/analyze         — AI анализ через Groq
  GET  /metadata            — метаданные слоёв
  GET  /layers              — список слоёв
  GET  /data/...            — статические файлы
  GET  /web/...             — фронтенд
"""

import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── TiTiler / rio-tiler ─────────────────────────────────────────
try:
    from rio_tiler.io import Reader
    from rio_tiler.errors import TileOutsideBounds
    import numpy
    TITILER_AVAILABLE = True
except ImportError:
    TITILER_AVAILABLE = False
    print("⚠️  titiler не установлен. pip install titiler.core --break-system-packages")

# Роутер анализа
try:
    from src.api.routes.analyze import router as analyze_router
    ANALYZE_AVAILABLE = True
except ImportError:
    ANALYZE_AVAILABLE = False
    print("⚠️  analyze.py не найден")

# ── Пути ────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent.parent   # src/api/main.py → корень
DATA_DIR  = BASE_DIR / "data"
WEB_DIR   = BASE_DIR / "web"

RASTER_DIRS = {
    "ndvi": DATA_DIR / "processed",
    "ndwi": DATA_DIR / "processed",
    "raw":  DATA_DIR / "raw",
    "web":  DATA_DIR / "web",
}

# ── App ─────────────────────────────────────────────────────────
app = FastAPI(
    title="GeoAI-TKO API",
    description="Геопространственная AI платформа для Туркестанской области",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500", "http://127.0.0.1:5500",
        "http://localhost:3000", "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

if ANALYZE_AVAILABLE:
    app.include_router(analyze_router)

# ════════════════════════════════════════════════════════════════
#   HEALTH
# ════════════════════════════════════════════════════════════════

@app.get("/health", tags=["system"])
async def health():
    cog_files_found = []
    if COG_DIR.exists():
        cog_files_found = [f.name for f in COG_DIR.glob("*.tif")]

    return {
        "status":  "ok",
        "service": "GeoAI-TKO",
        "version": "1.0.0",
        "data_dir_exists": DATA_DIR.exists(),
        "rasterio":  _check_import("rasterio"),
        "titiler":   TITILER_AVAILABLE,
        "groq":      _check_import("openai"),
        "api_key_set": bool(os.environ.get("GROQ_API_KEY")),
        "cog_files": cog_files_found,
        "cog_ready": len(cog_files_found) > 0,
    }

def _check_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False

# ════════════════════════════════════════════════════════════════
#   RASTERS (совместимость)
# ════════════════════════════════════════════════════════════════

@app.get("/rasters/{layer}", tags=["rasters"])
async def get_raster(layer: str, year: int = Query(2023, ge=2017, le=2025),
                     fmt: str = Query("png", alias="format")):
    ext = "png" if fmt == "png" else "tif"
    candidates = [
        DATA_DIR / "web" / layer / f"{layer}_{year}.{ext}",
        DATA_DIR / "web" / layer / f"{layer}.{ext}",
        DATA_DIR / "processed" / f"{layer}_tko_final.{ext}",
    ]
    for path in candidates:
        if path.exists():
            return FileResponse(str(path), media_type="image/png" if ext == "png" else "image/tiff",
                              headers={"Cache-Control": "public, max-age=3600"})
    raise HTTPException(404, f"Слой '{layer}' за {year} не найден")

# ════════════════════════════════════════════════════════════════
#   COG PATHS + TiTiler
# ════════════════════════════════════════════════════════════════

COG_DIR = DATA_DIR / "processed" / "cog"

# Используем ОРИГИНАЛЬНЫЕ GeoTIFF (не COG) — чтобы избежать warp-артефактов
COG_PATHS = {
    "ndvi": {2023: DATA_DIR / "processed" / "ndvi_tko_final.tif"},
    "ndwi": {2023: DATA_DIR / "processed" / "ndwi_tko_final.tif"},
}

def resolve_cog_path(layer: str, year: int) -> Path | None:
    paths = COG_PATHS.get(layer, {})
    if year in paths and paths[year].exists():
        return paths[year]
    for y in sorted(paths.keys(), key=lambda x: abs(x - year)):
        if paths[y].exists():
            return paths[y]
    return None

# Цветовые палитры
COLORMAPS = {
    "ndvi": {
        "stops": [(-0.2,(215,48,39,255)),(0.0,(253,174,97,255)),(0.2,(255,255,191,255)),
                  (0.4,(166,217,106,255)),(0.6,(26,152,80,255)),(0.8,(0,104,55,255))],
        "min": -0.2, "max": 0.8,
    },
    "ndwi": {
        "stops": [(-0.5,(247,251,255,255)),(0.0,(198,219,239,255)),(0.2,(107,174,214,255)),
                  (0.4,(33,113,181,255)),(0.6,(8,48,107,255))],
        "min": -0.5, "max": 0.6,
    },
}

def build_colormap(layer: str) -> dict:
    cfg = COLORMAPS.get(layer, COLORMAPS["ndvi"])
    stops, vmin, vmax = cfg["stops"], cfg["min"], cfg["max"]
    cmap = {}
    for i in range(256):
        val = vmin + (i / 255) * (vmax - vmin)
        for j in range(len(stops) - 1):
            v0, c0 = stops[j]; v1, c1 = stops[j + 1]
            if v0 <= val <= v1:
                t = (val - v0) / (v1 - v0) if v1 != v0 else 0
                cmap[i] = tuple(int(c0[k] + t * (c1[k] - c0[k])) for k in range(4))
                break
        else:
            cmap[i] = stops[0][1] if val < stops[0][0] else stops[-1][1]
    return cmap

_TRANSPARENT_PNG = None
def _transparent_png() -> bytes:
    global _TRANSPARENT_PNG
    if _TRANSPARENT_PNG is None:
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(buf, format="PNG")
        _TRANSPARENT_PNG = buf.getvalue()
    return _TRANSPARENT_PNG

if TITILER_AVAILABLE:
    @app.get("/cog/tiles/{layer}/{year}/{z}/{x}/{y}.png", tags=["tiles"])
    async def get_tile(layer: str, year: int, z: int, x: int, y: int):
        cog_path = resolve_cog_path(layer, year)
        if not cog_path:
            raise HTTPException(404, f"COG для {layer}/{year} не найден")

        vmin = COLORMAPS.get(layer, COLORMAPS["ndvi"])["min"]
        vmax = COLORMAPS.get(layer, COLORMAPS["ndvi"])["max"]

        try:
            with Reader(str(cog_path)) as cog:
                img = cog.tile(x, y, z, tilesize=256)
        except TileOutsideBounds:
            return Response(content=_transparent_png(), media_type="image/png")
        except Exception as e:
            print(f"Tile error {layer}/{z}/{x}/{y}: {e}")
            return Response(content=_transparent_png(), media_type="image/png")

        # Рендер через PIL напрямую — без rio-tiler render (обходим баг с альфа=0)
        from PIL import Image
        import io

        arr = img.data_as_image()  # (256, 256, 1)
        arr = arr[:, :, 0]  # -> (256, 256)

        # Заполнить NaN
        nan_mask = numpy.isnan(arr)
        arr[nan_mask] = vmin

        # Rescale 0-255
        arr_clipped = numpy.clip(arr, vmin, vmax)
        arr_byte = ((arr_clipped - vmin) / (vmax - vmin) * 255).astype(numpy.uint8)

        # Применить colormap
        cmap = build_colormap(layer)  # {0: (R,G,B,A), ...}
        lut = numpy.zeros((256, 4), dtype=numpy.uint8)
        for k, v in cmap.items():
            lut[k] = v

        rgba = lut[arr_byte]  # (256, 256, 4)

        # NoData → прозрачный
        rgba[nan_mask] = [0, 0, 0, 0]

        pil_img = Image.fromarray(rgba, 'RGBA')
        buf = io.BytesIO()
        pil_img.save(buf, format='PNG')
        content = buf.getvalue()

        return Response(content=content, media_type="image/png",
                       headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/cog/info", tags=["tiles"])
    async def cog_info(layer: str = Query("ndvi"), year: int = Query(2023)):
        cog_path = resolve_cog_path(layer, year)
        if not cog_path:
            raise HTTPException(404, f"COG для {layer}/{year} не найден")
        with Reader(str(cog_path)) as cog:
            info = cog.info()
            from pyproj import Transformer
            native_crs = cog.dataset.crs
            t = Transformer.from_crs(native_crs, "EPSG:4326", always_xy=True)
            west, south = t.transform(info.bounds[0], info.bounds[1])
            east, north = t.transform(info.bounds[2], info.bounds[3])
            return {
                "bounds": [west, south, east, north],
                "minzoom": 6, "maxzoom": 16,
                "width": info.width, "height": info.height,
                "resolution_m": abs(cog.dataset.res[0]),
            }

# ════════════════════════════════════════════════════════════════
#   STATIC
# ════════════════════════════════════════════════════════════════

if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")
if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

# ════════════════════════════════════════════════════════════════
#   METADATA / LAYERS
# ════════════════════════════════════════════════════════════════

@app.get("/metadata", tags=["system"])
async def metadata():
    return {
        "region": {"name": "Туркестанская область, Казахстан",
                   "bounds": [[40.8, 67.5], [44.0, 71.5]],
                   "crs": "EPSG:4326", "area_km2": 116000},
        "layers": {
            "ndvi": {"available_years": [2023], "resolution_m": 10},
            "ndwi": {"available_years": [2023], "resolution_m": 10},
        },
        "source": "Sentinel-2 SR via Google Earth Engine",
        "last_update": "2024-11",
    }

@app.get("/layers", tags=["system"])
async def layers():
    return {"layers": ["ndvi", "ndwi"], "active": "ndvi"}

# ── Dev ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
