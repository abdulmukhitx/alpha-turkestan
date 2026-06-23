"""
Запусти один раз:
    python patch_fix.py
Это исправит data_from_script.py — добавит правильный маппинг банд.
"""
import re
import os

TARGET = os.path.join(os.path.dirname(__file__), "data_from_script.py")

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

NEW_BLOCK = '''# ===========================================================================
# 2. Asset URL resolution
# ===========================================================================

# Element84 Earth Search использует человекочитаемые ключи вместо "B05" и т.д.
BAND_ALIAS = {
    "B01": ["coastal",  "B01", "b01"],
    "B02": ["blue",     "B02", "b02"],
    "B03": ["green",    "B03", "b03"],
    "B04": ["red",      "B04", "b04"],
    "B05": ["rededge1", "B05", "b05"],
    "B06": ["rededge2", "B06", "b06"],
    "B07": ["rededge3", "B07", "b07"],
    "B08": ["nir",      "B08", "b08"],
    "B8A": ["nir08",    "B8A", "b8a"],
    "B09": ["nir09",    "B09", "b09"],
    "B11": ["swir16",   "B11", "b11"],
    "B12": ["swir22",   "B12", "b12"],
    "SCL": ["scl",      "SCL"],
    "AOT": ["aot",      "AOT"],
    "WVP": ["wvp",      "WVP"],
}


def get_asset_href(item, band):
    assets = item.assets if hasattr(item, "assets") else item.get("assets", {})
    candidates = BAND_ALIAS.get(band.upper(), [band, band.lower(), band.upper()])
    for key in candidates:
        if key in assets:
            asset = assets[key]
            href = asset.href if hasattr(asset, "href") else asset.get("href")
            if href:
                return href
    log.warning("Band '%s' not found. Available: %s", band, list(assets.keys()))
    return None

'''

# Заменяем весь блок от "# 2. Asset URL" до "# 3. Download"
pattern = r'# =+\n# 2\. Asset URL resolution.*?(?=# =+\n# 3\. Download)'
result = re.sub(pattern, NEW_BLOCK, content, flags=re.DOTALL)

if result == content:
    print("ОШИБКА: паттерн не найден. Проверь структуру файла.")
else:
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(result)
    print("OK — data_from_script.py успешно обновлён!")
    print("Проверка: 'rededge1' есть в файле:", "rededge1" in result)
