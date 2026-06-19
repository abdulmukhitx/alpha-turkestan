"""
GeoAI-TKO · src/api/routes/analyze.py

Два endpoint-а:
  GET  /api/pixel   — значения индексов для (lat, lon, year)
  POST /api/analyze — AI интерпретация через DeepSeek API
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from openai import OpenAI
from pathlib import Path
import os
import traceback
from dotenv import load_dotenv
load_dotenv()

# ── Опциональный импорт rasterio ───────────────────────────────
try:
    import rasterio
    import numpy as np
    RASTERIO_AVAILABLE = True
except ImportError:
    RASTERIO_AVAILABLE = False
    print("⚠️  rasterio не установлен — pixel endpoint работает в demo-режиме")

# ── DeepSeek client (OpenAI-compatible) ─────────────────────────
from openai import OpenAI

def get_deepseek_client():
    return OpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
    )

router = APIRouter(prefix="/api", tags=["analysis"])

# ── Пути к растровым файлам ──────────────────────────────────────
# Используем финальные файлы

# Fallback: если нет tif по году — ищем любой доступный
def find_raster(layer: str, year: int) -> str | None:
    # Используем финальные файлы
    base = Path(__file__).resolve().parents[3] / "data" / "processed"
    mapping = {
        "ndvi": base / "ndvi_tko_final.tif",
        "ndwi": base / "ndwi_tko_final.tif",
    }
    path = mapping.get(layer)
    if path and path.exists():
        return str(path)
    return None


# ════════════════════════════════════════════════════════════════
#   GET /api/pixel
# ════════════════════════════════════════════════════════════════

@router.get("/pixel")
async def get_pixel_values(
    lat:   float = Query(..., ge=40.8, le=44.0, description="Широта"),
    lon:   float = Query(..., ge=67.5, le=71.5, description="Долгота"),
    year:  int   = Query(2023, ge=2017, le=2025, description="Год данных"),
):
    """
    Возвращает значения спектральных индексов для пикселя (lat, lon).
    
    Если rasterio доступен — читает из реальных GeoTIFF.
    Иначе — возвращает демо-данные (для разработки).
    """
    ndvi_val = None
    ndwi_val = None

    if RASTERIO_AVAILABLE:
        # ── Читаем NDVI ──
        ndvi_path = find_raster("ndvi", year)
        if ndvi_path:
            try:
                ndvi_val = read_pixel_value(ndvi_path, lat, lon)
            except Exception as e:
                print(f"NDVI read error: {e}")

        # ── Читаем NDWI ──
        ndwi_path = find_raster("ndwi", year)
        if ndwi_path:
            try:
                ndwi_val = read_pixel_value(ndwi_path, lat, lon)
            except Exception as e:
                print(f"NDWI read error: {e}")
    else:
        # Demo значения
        ndvi_val, ndwi_val = _demo_pixel(lat, lon)

    # Определить класс покрытия по значениям
    land_class, trend_label = classify_pixel(ndvi_val, ndwi_val)

    # Тренд по годам (если есть данные)
    trend_series = build_trend_series(lat, lon, layer="ndvi")

    import math
    def safe_val(v):
        if v is None: return None
        if isinstance(v, float) and math.isnan(v): return None
        return round(v, 3)

    return {
        "lat":        lat,
        "lon":        lon,
        "year":       year,
        "ndvi":       safe_val(ndvi_val),
        "ndwi":       safe_val(ndwi_val),
        "land_class": land_class,
        "trend_label": trend_label,
        "trend":      [safe_val(v) for v in trend_series],
        "recommendations": build_recommendations(ndvi_val, ndwi_val, land_class),
    }


def read_pixel_value(tif_path: str, lat: float, lon: float) -> float:
    """Читает значение пикселя из GeoTIFF."""
    with rasterio.open(tif_path) as src:
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:4326", src.crs.to_epsg(), always_xy=True)
        x, y = transformer.transform(lon, lat)
        row, col = src.index(x, y)
        if row < 0 or row >= src.height or col < 0 or col >= src.width:
            raise ValueError(f"Точка вне растра")
        val = float(src.read(1, window=((row, row+1), (col, col+1)))[0, 0])
        if src.nodata is not None and val == src.nodata:
            raise ValueError("NoData")
        return val


def classify_pixel(ndvi, ndwi):
    """Простая эвристическая классификация по NDVI/NDWI."""
    if ndvi is None:
        return "Нет данных", "—"

    if ndwi is not None and ndwi > 0.2:
        return "Водная поверхность", "→ стабильно"
    if ndvi > 0.5:
        return "Густая растительность / лес", "↑ активный рост"
    if ndvi > 0.3:
        return "Ирригированное поле", "↑ хорошее состояние"
    if ndvi > 0.15:
        return "Пастбище / разреженная растительность", "→ умеренное"
    if ndvi > 0.05:
        return "Деградирующие земли", "↓ требует мониторинга"
    if ndvi < 0:
        return "Вода / солончак", "→ стабильно"
    return "Голая почва / пустыня", "→ минимальная активность"


def build_trend_series(lat: float, lon: float, layer: str = "ndvi") -> list[float]:
    """
    Строит временной ряд значений для mini-chart.
    Сейчас — демо. В production: читать из всех годовых файлов.
    """
    if not RASTERIO_AVAILABLE:
        return _demo_trend(lat, lon)

    series = []
    for year in range(2017, 2024):
        path = find_raster(layer, year)
        if path:
            try:
                val = read_pixel_value(path, lat, lon)
                series.append(round(val, 3))
            except:
                series.append(None)
        else:
            series.append(None)

    # Заполнить None интерполяцией
    series = interpolate_nones(series)
    return series


def interpolate_nones(series: list) -> list:
    """Простая линейная интерполяция пропусков."""
    result = list(series)
    n = len(result)
    for i in range(n):
        if result[i] is None:
            # Найти предыдущий и следующий не-None
            prev_i = next((j for j in range(i-1, -1, -1) if result[j] is not None), None)
            next_i = next((j for j in range(i+1, n)  if result[j] is not None), None)
            if prev_i is not None and next_i is not None:
                t = (i - prev_i) / (next_i - prev_i)
                result[i] = round(result[prev_i] + t * (result[next_i] - result[prev_i]), 3)
            elif prev_i is not None:
                result[i] = result[prev_i]
            elif next_i is not None:
                result[i] = result[next_i]
    return [v for v in result if v is not None]


def build_recommendations(ndvi, ndwi, land_class: str) -> list[str]:
    recs = []
    if ndvi is not None:
        if ndvi < 0.1:
            recs.append("Риск опустынивания — рекомендуется полевое обследование")
        elif ndvi < 0.25:
            recs.append("Проверить состояние ирригации в данном участке")
        if ndvi > 0.45:
            recs.append("Продуктивная зона — потенциал для мониторинга урожайности")
    if ndwi is not None and ndwi > 0.15:
        recs.append("Обнаружена влажность — проверить утечки ирригационных каналов")
    return recs


def _demo_pixel(lat: float, lon: float):
    """Детерминированные демо-значения для разработки."""
    import math
    seed = abs(math.sin(lat * 100) * math.cos(lon * 100))
    ndvi = round(0.05 + seed * 0.65, 3)
    ndwi = round(-0.3 + seed * 0.5,  3)
    return ndvi, ndwi


def _demo_trend(lat: float, lon: float):
    import math
    base = abs(math.sin(lat * 50) * 0.4) + 0.1
    return [round(base + math.sin(i * 0.8) * 0.06, 3) for i in range(7)]


# ════════════════════════════════════════════════════════════════
#   POST /api/analyze
# ════════════════════════════════════════════════════════════════

class AnalyzeRequest(BaseModel):
    lat:        float = Field(..., ge=40.8, le=44.0)
    lon:        float = Field(..., ge=67.5, le=71.5)
    year:       int   = Field(2023)
    layer:      str   = Field("ndvi")
    ndvi:       float | None = None
    ndwi:       float | None = None
    land_class: str   | None = None


@router.post("/analyze")
async def analyze_point(req: AnalyzeRequest):
    """
    Отправляет данные о точке в DeepSeek API и возвращает AI интерпретацию.
    FastAPI выступает прокси — ключ API остаётся на сервере.
    """
    prompt = _build_analysis_prompt(req)

    try:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY не задан в .env")
        
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

        response = client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            max_tokens=350,
            temperature=0.3,
            messages=[
                {"role": "system", "content": (
                    "Ты — эксперт по дистанционному зондированию Земли и агрономии "
                    "Центральной Азии. Анализируешь спутниковые данные Туркестанской области "
                    "Казахстана. Отвечай кратко (2–4 предложения), конкретно, "
                    "на русском языке. Не используй технический жаргон без необходимости. "
                    "Сосредоточься на практическом значении данных."
                )},
                {"role": "user", "content": prompt},
            ],
        )

        analysis_text = response.choices[0].message.content
        # Fix encoding if needed
        if isinstance(analysis_text, str):
            analysis_text = analysis_text.encode('utf-8', errors='replace').decode('utf-8')

    except Exception as e:
        print(f"DeepSeek API error: {traceback.format_exc()}")
        analysis_text = _fallback_analysis(req)

    return {"analysis": analysis_text}


def get_groq_client() -> OpenAI:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")
    return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")


def _build_analysis_prompt(req: AnalyzeRequest) -> str:
    parts = [
        f"Проанализируй спутниковые данные для точки с координатами "
        f"{req.lat:.4f}°N, {req.lon:.4f}°E в Туркестанской области Казахстана, {req.year} год.",
    ]

    if req.ndvi is not None:
        parts.append(f"NDVI = {req.ndvi:.3f} (индекс растительности).")
    if req.ndwi is not None:
        parts.append(f"NDWI = {req.ndwi:.3f} (водный индекс).")
    if req.land_class:
        parts.append(f"Автоматически определённый класс: {req.land_class}.")

    parts.append(
        "Дай краткую интерпретацию: что происходит на этом участке, "
        "какова вероятная причина таких значений, и что это означает "
        "для землепользования или мониторинга окружающей среды?"
    )

    return " ".join(parts)


def _fallback_analysis(req: AnalyzeRequest) -> str:
    """Локальный анализ без Claude API."""
    ndvi = req.ndvi
    if ndvi is None:
        return "Данные для анализа недоступны. Попробуйте выбрать другую точку."

    if ndvi > 0.5:
        return (
            f"Участок ({req.lat:.3f}°N) демонстрирует высокий индекс растительности "
            f"(NDVI={ndvi:.2f}), характерный для активно орошаемых полей или "
            f"пойменной растительности вдоль Сырдарьи. Состояние растительного "
            f"покрова оценивается как хорошее."
        )
    elif ndvi > 0.25:
        return (
            f"Умеренный NDVI={ndvi:.2f} указывает на разреженную или сезонную "
            f"растительность. Характерно для пастбищных угодий или полей в начале "
            f"вегетационного сезона. Рекомендуется дополнительный мониторинг."
        )
    else:
        return (
            f"Низкий NDVI={ndvi:.2f} свидетельствует об отсутствии активной "
            f"растительности. Типично для пустынных и деградированных земель "
            f"Туркестанской области. Возможны процессы засоления или опустынивания."
        )
