"""
GeoAI-TKO - src/processing/download_orbit_patch.py
=======================================================
Точечный patch для orbit-seam дырок, обнаруженных после первой пересборки
2025_summer — top-3-by-cloud отбор кандидатов (до фикса в
find_composite_candidates.py) мог взять все 3 продукта с одного и того же
relative orbit, оставляя целую половину тайла (соответствующую ДРУГОМУ
orbit'у) в честном nodata. Диагностика (geometric coverage check против
STAC item geometry) нашла для каждого такого тайла конкретный orbit,
покрывающий 95%+ пропавшей зоны.

НЕ трогает основной manifest.json/composite_candidates.json/fill_manifest.json —
сохраняет в отдельную папку patch_orbit/ для последующего точечного
patch-merge после того как основная пересборка (Фаза A+B) завершится.

Запуск:
  python src/processing/download_orbit_patch.py
"""
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
import os

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
RAW_DIR = Path(r"D:\data\s2_2025_raw")

ASSET_TO_BAND = {
    "B02_10m": "B02", "B03_10m": "B03", "B04_10m": "B04", "B08_10m": "B08",
    "B05_20m": "B05", "B8A_20m": "B8A", "B11_20m": "B11",
}

# From gap_diagnosis.json (Шаг: geometric coverage check) — 5 remaining tiles.
# 42TVN/42TUL patches already downloaded earlier (patch_orbit34/), kept as-is.
PATCH_PRODUCTS = {
    "42TXM": "S2A_MSIL2A_20250830T061301_N0511_R134_T42TXM_20250830T093502",  # placeholder, overwritten below by exact STAC id
    "42TUR": None,
    "42TWR": None,
    "42TWK": None,
    "42TWN": None,
}

ITEM_URL_TMPL = "https://catalogue.dataspace.copernicus.eu/stac/collections/sentinel-2-l2a/items/{id}"
STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"


class TokenManager:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.token = None
        self.expires_at = 0

    def get(self):
        if self.token is None or time.time() > self.expires_at - 180:
            r = requests.post(TOKEN_URL, data={
                "grant_type": "password", "username": self.username,
                "password": self.password, "client_id": "cdse-public",
            }, timeout=30)
            r.raise_for_status()
            d = r.json()
            self.token = d["access_token"]
            self.expires_at = time.time() + d["expires_in"]
            print(f"[auth] Токен получен, истекает через {d['expires_in']}s")
        return self.token


def download_asset(url, dest, token_mgr, expected_size=None):
    for attempt in range(1, 4):
        try:
            token = token_mgr.get()
            r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=120, stream=True)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
            size = tmp.stat().st_size
            if expected_size is not None and size != expected_size:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"size mismatch: {size} != {expected_size}")
            tmp.replace(dest)
            return size
        except Exception as e:
            wait = 2 ** attempt
            print(f"    [retry {attempt}/3] {dest.name}: {e} — жду {wait}s")
            if attempt == 3:
                raise
            time.sleep(wait)


def main():
    import json
    diag = json.loads(Path(r"D:\data\gap_diagnosis.json").read_text(encoding="utf-8"))
    product_ids = {d["tile"]: d["product_id"] for d in diag if d.get("orbit")}
    print(f"Продукты к скачиванию: {product_ids}")

    user = os.environ.get("CDSE_USERNAME", "").strip()
    pwd = os.environ.get("CDSE_PASSWORD", "").strip()
    token_mgr = TokenManager(user, pwd)
    token_mgr.get()

    for tile, pid in product_ids.items():
        print(f"\n{tile}: {pid}")
        r = requests.get(ITEM_URL_TMPL.format(id=pid), timeout=30)
        r.raise_for_status()
        assets = r.json()["assets"]

        patch_dir = RAW_DIR / tile / "patch_orbit34"
        patch_dir.mkdir(parents=True, exist_ok=True)

        for asset_key, band_name in ASSET_TO_BAND.items():
            a = assets.get(asset_key)
            if not a:
                print(f"  ОШИБКА: {asset_key} отсутствует")
                continue
            href = a["alternate"]["https"]["href"]
            expected_size = a.get("file:size")
            dest = patch_dir / f"{band_name}.jp2"
            if dest.exists() and expected_size and dest.stat().st_size == expected_size:
                print(f"  {band_name}: уже скачан")
                continue
            size = download_asset(href, dest, token_mgr, expected_size)
            print(f"  {band_name}: {size/1e6:.1f} MB")

    print("\nГотово. Патч-продукты в D:\\data\\s2_2025_raw\\{tile}\\patch_orbit34\\")


if __name__ == "__main__":
    main()
