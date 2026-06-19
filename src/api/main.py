"""
GeoAI-TKO · src/api/main.py

Запуск: uvicorn src.api.main:app --reload --port 8000

Структура:
  GET  /health            — проверка сервиса
  GET  /rasters/{layer}   — PNG растра для Leaflet imageOverlay
  GET  /api/pixel         — значения индексов для точки
  POST /api/analyze       — AI анализ через Claude
  GET  /data/...          — статические файлы (GeoJSON границы и т.д.)
"""

import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Роутер анализа — подключаем из соседнего файла
try:
    from src.api.routes.analyze import router as analyze_router
    ANALYZE_AVAILABLE = True
except ImportError:
    ANALYZE_AVAILABLE = False
    print("⚠️  analyze.py не найден, /api/* endpoints отключены")

# ── Пути к данным ────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent.parent   # корень проекта
DATA_DIR  = BASE_DIR / "data"
WEB_DIR   = BASE_DIR / "web"                     # фронтенд (index.html, app.js, style.css)

# Если main.py запускается из корня проекта напрямую:
if not BASE_DIR.exists():
    BASE_DIR = Path(".")
    DATA_DIR = Path("data")
    WEB_DIR  = Path("web")

RASTER_DIRS = {
    "ndvi":   DATA_DIR / "processed",
    "ndwi":   DATA_DIR / "processed",
    "raw":    DATA_DIR / "raw",
    "web":    DATA_DIR / "web",
}

# ── App ─────────────────────────────────────────────────────────
app = FastAPI(
    title="GeoAI-TKO API",
    description="Геопространственная AI платформа для Туркестанской области",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS — разрешаем фронтенду на localhost:5500 (Live Server) и prod
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        # Добавь свой домен в продакшне:
        # "https://geoai-tko.kz",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Подключить роутер анализа
if ANALYZE_AVAILABLE:
    app.include_router(analyze_router)

# ════════════════════════════════════════════════════════════════
#   HEALTH
# ════════════════════════════════════════════════════════════════

@app.get("/health", tags=["system"])
async def health():
    """Проверка состояния сервиса."""
    return {
        "status":  "ok",
        "service": "GeoAI-TKO",
        "version": "1.0.0",
        "data_dir_exists": DATA_DIR.exists(),
        "rasterio":        _check_import("rasterio"),
        "groq":            _check_import("openai"),
        "api_key_set":     bool(os.environ.get("GROQ_API_KEY")),
    }

def _check_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False

# ════════════════════════════════════════════════════════════════
#   RASTERS — отдаём PNG / TIFF файлы для Leaflet imageOverlay
# ════════════════════════════════════════════════════════════════

@app.get("/rasters/{layer}", tags=["rasters"])
async def get_raster(
    layer: str,
    year:   int = Query(2023, ge=2017, le=2025),
    fmt:    str = Query("png", alias="format", description="png или tif"),
):
    """
    Отдаёт растровый файл слоя для отображения в Leaflet imageOverlay.
    
    Листлет использует imageOverlay(url, bounds) — просто PNG файл достаточно.
    
    Файлы ищутся в такой очерёдности:
      data/web/{layer}/     — предварительно экспортированные PNG тайлы
      data/processed/       — GeoTIFF (если png нужно сконвертировать)
      data/raw/             — сырые данные
    """
    ext = "png" if fmt == "png" else "tif"

    # Ищем файл по нескольким путям
    candidates = [
        DATA_DIR / "web"  / layer / f"{layer}_{year}.{ext}",
        DATA_DIR / "web"  / layer / f"{layer}.{ext}",
        DATA_DIR / "web"  / f"{layer}_{year}.{ext}",
        DATA_DIR / "web"  / f"{layer}.{ext}",
        DATA_DIR / "processed" / f"{layer}_tko_{year}.{ext}",
        DATA_DIR / "processed" / f"{layer}_tko_final.{ext}",
        DATA_DIR / "processed" / f"{layer}_tko.{ext}",
        DATA_DIR / "raw"  / f"{layer}_{year}.{ext}",
        # Твои текущие файлы:
        DATA_DIR / "raw"  / f"{layer}_2023_full.tif",
        DATA_DIR / f"{layer}_tko_final.png",
    ]

    for path in candidates:
        if path.exists():
            media_type = "image/png" if ext == "png" else "image/tiff"
            return FileResponse(
                str(path),
                media_type=media_type,
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Access-Control-Allow-Origin": "*",
                },
            )

    # Файл не найден — возвращаем прозрачный PNG 1x1 как fallback
    print(f"⚠️  Растр не найден: layer={layer}, year={year}")
    raise HTTPException(
        status_code=404,
        detail=f"Слой '{layer}' за {year} год не найден. "
               f"Проверьте наличие файлов в data/web/{layer}/ или data/processed/"
    )

# ════════════════════════════════════════════════════════════════
#   STATIC FILES — данные (GeoJSON, PNG для фронтенда)
# ════════════════════════════════════════════════════════════════

# Отдаём файлы из data/ как /data/...
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

# Фронтенд — если хочешь раздавать index.html через FastAPI:
# (иначе используй VS Code Live Server или отдельный nginx)
if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

# ════════════════════════════════════════════════════════════════
#   METADATA
# ════════════════════════════════════════════════════════════════

@app.get("/metadata", tags=["system"])
async def metadata():
    """Метаданные доступных слоёв и временных периодов."""
    return {
        "region": {
            "name":   "Туркестанская область, Казахстан",
            "bounds": [[40.8, 67.5], [44.0, 71.5]],
            "crs":    "EPSG:4326",
            "area_km2": 116000,
        },
        "layers": {
            "ndvi":   {"available_years": _scan_years("ndvi"),   "resolution_m": 10},
            "ndwi":   {"available_years": _scan_years("ndwi"),   "resolution_m": 10},
            "lst":    {"available_years": _scan_years("lst"),     "resolution_m": 30},
            "change": {"available_years": _scan_years("change"), "resolution_m": 10},
        },
        "source":     "Sentinel-2 SR via Google Earth Engine",
        "last_update": "2024-11",
    }

def _scan_years(layer: str) -> list[int]:
    """Находит все доступные годы для слоя."""
    years = []
    search_dir = DATA_DIR / "web" / layer
    if search_dir.exists():
        for f in search_dir.glob(f"{layer}_*.png"):
            try:
                year = int(f.stem.split("_")[-1])
                if 2017 <= year <= 2025:
                    years.append(year)
            except ValueError:
                pass
    return sorted(years) or [2023]   # fallback

# ════════════════════════════════════════════════════════════════
#   LAYERS (совместимость с существующим /layers endpoint)
# ════════════════════════════════════════════════════════════════

@app.get("/layers", tags=["system"])
async def layers():
    """Список доступных слоёв (совместимость с существующим API)."""
    return {
        "layers": ["ndvi", "ndwi", "lst", "change"],
        "active": "ndvi",
    }


# ── Dev запуск ──────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
