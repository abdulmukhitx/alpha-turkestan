"""
GeoAI-TKO · scripts/convert_to_cog.py

Конвертирует обычный GeoTIFF (NDVI/NDWI) в Cloud Optimized GeoTIFF.

Зачем: твой ndvi_2023_full.tif весит 2+ ГБ и не оптимизирован.
TiTiler сможет отдавать тайлы с него, но будет читать файл целиком
на каждый запрос. После конвертации в COG — TiTiler читает только
нужные ~256x256 пикселей через HTTP range requests, тайл грузится
за миллисекунды вместо секунд.

Запуск:
    python scripts/convert_to_cog.py

Требует: rasterio, rio-cogeo
    pip install rasterio rio-cogeo --break-system-packages
"""

import os
import sys
from pathlib import Path

try:
    import rasterio
    from rio_cogeo.cogeo import cog_translate
    from rio_cogeo.profiles import cog_profiles
except ImportError:
    print("❌ Не установлены зависимости. Выполни:")
    print("   pip install rasterio rio-cogeo --break-system-packages")
    sys.exit(1)


# ── Конфигурация: какие файлы конвертировать ────────────────────
# Подправь под свои реальные пути из структуры проекта
CONVERSIONS = [
    {
        "input":  "data/processed/ndvi_tko_final.tif",
        "output": "data/processed/cog/ndvi_2023_cog.tif",
        "name":   "NDVI 2023",
    },
    {
        "input":  "data/processed/ndwi_tko_final.tif",
        "output": "data/processed/cog/ndwi_2023_cog.tif",
        "name":   "NDWI 2023",
    },
]


def convert_to_cog(input_path: str, output_path: str, name: str):
    """Конвертирует один GeoTIFF в COG с компрессией и overview-пирамидой."""

    if not os.path.exists(input_path):
        print(f"⚠️  Пропускаю {name}: файл не найден ({input_path})")
        return False

    in_size_mb = os.path.getsize(input_path) / (1024 * 1024)
    print(f"\n📦 {name}")
    print(f"   Вход:  {input_path} ({in_size_mb:.0f} МБ)")

    # Создать выходную директорию
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Профиль сжатия — DEFLATE с предиктором для float32 данных
    # (NDVI/NDWI обычно float32, predictor=3 эффективнее для float)
    dst_profile = cog_profiles.get("deflate")
    dst_profile.update({
        "BLOCKXSIZE": 512,
        "BLOCKYSIZE": 512,
        "PREDICTOR":  3,        # 3 = floating point predictor
    })

    cog_translate(
        input_path,
        output_path,
        dst_profile,
        in_memory=False,        # важно при 2+ ГБ — не грузить весь файл в RAM
        quiet=False,
        overview_resampling="average",
        web_optimized=True,     # выравнивает по веб-меркаторной тайловой сетке
    )

    out_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    reduction = (1 - out_size_mb / in_size_mb) * 100

    print(f"   Выход: {output_path} ({out_size_mb:.0f} МБ)")
    print(f"   ✅ Сжатие: {reduction:.0f}%")

    return True


def validate_cog(path: str):
    """Проверяет что файл — валидный COG."""
    try:
        from rio_cogeo.cogeo import cog_validate
        is_valid, errors, warnings = cog_validate(path)
        if is_valid:
            print(f"   ✅ Валидный COG")
        else:
            print(f"   ❌ Невалидный COG: {errors}")
        if warnings:
            print(f"   ⚠️  Предупреждения: {warnings}")
        return is_valid
    except ImportError:
        print("   (валидация пропущена — rio-cogeo.cogeo.cog_validate недоступен)")
        return True


if __name__ == "__main__":
    print("═" * 60)
    print("  GeoAI-TKO · Конвертация GeoTIFF → COG")
    print("═" * 60)

    success_count = 0
    for conv in CONVERSIONS:
        ok = convert_to_cog(conv["input"], conv["output"], conv["name"])
        if ok:
            validate_cog(conv["output"])
            success_count += 1

    print("\n" + "═" * 60)
    print(f"  Готово: {success_count}/{len(CONVERSIONS)} файлов сконвертировано")
    print("═" * 60)

    if success_count > 0:
        print("\n📍 Следующий шаг: запусти TiTiler сервер")
        print("   uvicorn src.api.main:app --reload --port 8000")
