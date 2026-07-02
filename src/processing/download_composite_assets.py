"""
GeoAI-TKO - src/processing/download_composite_assets.py
=======================================================
ЗАДАЧА 2, Шаг 2.1 (докачка) - для каждого из 43 тайлов скачивает ДВА
дополнительных продукта (fill1, fill2) с отличной от оригинального primary
датой съёмки — источники для заполнения orbit-edge дырок в Задаче 2.2.

Оригинальный primary (уже скачан в Этапе 4, D:\\data\\s2_2025_raw\\{tile}\\*.jp2)
НЕ перекачивается повторно — берём его как есть. Из composite_candidates.json
(top-3 по возрастанию облачности) выбираем 2 кандидата, чей product_id
отличается от original_manifest_product_id, и качаем их JP2-бэнды в
D:\\data\\s2_2025_raw\\{tile}\\fill1\\ и \\fill2\\.

Запуск:
  python src/processing/download_composite_assets.py > D:\\data\\s2_2025_raw\\download_fill.log 2>&1
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
import os

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
ITEM_URL_TMPL = "https://catalogue.dataspace.copernicus.eu/stac/collections/sentinel-2-l2a/items/{id}"

CANDIDATES_PATH = Path(r"D:\data\s2_2025_raw\composite_candidates.json")
RAW_DIR = Path(r"D:\data\s2_2025_raw")
FILL_MANIFEST_PATH = RAW_DIR / "fill_manifest.json"

ASSET_TO_BAND = {
    "B02_10m": "B02", "B03_10m": "B03", "B04_10m": "B04", "B08_10m": "B08",
    "B05_20m": "B05", "B8A_20m": "B8A", "B11_20m": "B11",
}
MAX_RETRIES = 3
TOKEN_REFRESH_MARGIN_S = 180

HIGH_PERFORMANCE_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def check_power_plan():
    import subprocess
    out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True,
                          errors="ignore").stdout
    print(f"[preflight] powercfg /getactivescheme -> {out.strip()}")
    if HIGH_PERFORMANCE_GUID not in out.lower():
        print(f"[preflight] ОШИБКА: активная схема не High Performance ({HIGH_PERFORMANCE_GUID}).")
        sys.exit(1)
    print("[preflight] OK — план питания 'Высокая производительность' активен.")


class TokenManager:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.token = None
        self.expires_at = 0

    def get(self):
        if self.token is None or time.time() > self.expires_at - TOKEN_REFRESH_MARGIN_S:
            r = requests.post(
                TOKEN_URL,
                data={"grant_type": "password", "username": self.username,
                      "password": self.password, "client_id": "cdse-public"},
                timeout=30,
            )
            r.raise_for_status()
            d = r.json()
            self.token = d["access_token"]
            self.expires_at = time.time() + d["expires_in"]
            print(f"[auth] Новый токен получен, истекает через {d['expires_in']}s")
        return self.token


def fetch_item_assets(product_id: str, token_mgr: TokenManager) -> dict:
    r = requests.get(ITEM_URL_TMPL.format(id=product_id), timeout=30)
    r.raise_for_status()
    return r.json()["assets"]


def download_asset(url, dest, token_mgr, expected_size=None):
    for attempt in range(1, MAX_RETRIES + 1):
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
                raise RuntimeError(f"size mismatch: got {size}, expected {expected_size}")
            tmp.replace(dest)
            return size
        except Exception as e:
            wait = 2 ** attempt
            print(f"    [retry {attempt}/{MAX_RETRIES}] {dest.name}: {e} — жду {wait}s")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(wait)


def verify_tif(path: Path) -> bool:
    try:
        import rasterio
        from rasterio.windows import Window
        with rasterio.open(path) as ds:
            if ds.width <= 0 or ds.height <= 0:
                return False
            w = min(64, ds.width)
            h = min(64, ds.height)
            ds.read(1, window=Window(0, 0, w, h))
        return True
    except Exception as e:
        print(f"    [verify] {path.name}: FAILED ({e})")
        return False


def main():
    t0 = time.time()
    print("=" * 70)
    print("  GeoAI-TKO: докачка fill1/fill2 продуктов для compositing")
    print("=" * 70)

    check_power_plan()
    user = os.environ.get("CDSE_USERNAME", "").strip()
    pwd = os.environ.get("CDSE_PASSWORD", "").strip()
    if not user or not pwd:
        print("ОШИБКА: CDSE_USERNAME/CDSE_PASSWORD не заданы.")
        sys.exit(1)
    token_mgr = TokenManager(user, pwd)
    token_mgr.get()

    candidates = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))["tiles"]

    # build the download plan: 2 fill products per tile, distinct from original primary
    plan = {}
    for tile, entry in sorted(candidates.items()):
        original_pid = entry["original_manifest_product_id"]
        fills = [c for c in entry["candidates"] if c["product_id"] != original_pid][:2]
        plan[tile] = fills

    total_products = sum(len(v) for v in plan.values())
    print(f"\nПлан: {total_products} доп. продуктов ({sum(1 for v in plan.values() if len(v)>=1)} тайлов с >=1 fill, "
          f"{sum(1 for v in plan.values() if len(v)>=2)} тайлов с 2 fill)")

    fill_manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "tiles": {}}
    downloaded_bytes = 0
    ok_count = failed_count = 0

    idx = 0
    for tile, fills in plan.items():
        fill_manifest["tiles"][tile] = []
        for slot, cand in enumerate(fills, 1):
            idx += 1
            pid = cand["product_id"]
            print(f"\n[{idx}/{total_products}] Тайл {tile} fill{slot} | {pid} | CC={cand['cloud_cover']}%")

            fill_dir = RAW_DIR / tile / f"fill{slot}"
            fill_dir.mkdir(parents=True, exist_ok=True)

            try:
                assets = fetch_item_assets(pid, token_mgr)
            except Exception as e:
                print(f"    ОШИБКА получения ассетов: {e}")
                failed_count += 1
                continue

            band_files = {}
            tile_ok = True
            for asset_key, band_name in ASSET_TO_BAND.items():
                a = assets.get(asset_key)
                if a is None:
                    print(f"    ОШИБКА: ассет {asset_key} отсутствует")
                    tile_ok = False
                    break
                href = a["alternate"]["https"]["href"]
                expected_size = a.get("file:size")
                dest = fill_dir / f"{band_name}.jp2"

                if dest.exists() and expected_size and dest.stat().st_size == expected_size:
                    print(f"    {band_name}: уже скачан, пропускаю")
                    size = expected_size
                else:
                    try:
                        size = download_asset(href, dest, token_mgr, expected_size)
                    except Exception as e:
                        print(f"    ОШИБКА скачивания {band_name}: {e}")
                        tile_ok = False
                        break

                if not verify_tif(dest):
                    print(f"    ОШИБКА: {band_name} не прошёл проверку целостности")
                    tile_ok = False
                    break

                band_files[band_name] = str(dest)
                downloaded_bytes += size

            if tile_ok:
                ok_count += 1
                fill_manifest["tiles"][tile].append({
                    "slot": f"fill{slot}",
                    "product_id": pid,
                    "cloud_cover": cand["cloud_cover"],
                    "datetime": cand["datetime"],
                    "bands": band_files,
                })
            else:
                failed_count += 1

            elapsed = time.time() - t0
            print(f"    Прогресс: {idx}/{total_products}, {downloaded_bytes/1e9:.2f} GB, {elapsed/60:.1f} мин")

    FILL_MANIFEST_PATH.write_text(json.dumps(fill_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 70)
    print("  ИТОГ докачки fill-продуктов")
    print("=" * 70)
    print(f"  Успешно: {ok_count}/{total_products}")
    print(f"  Провалено: {failed_count}/{total_products}")
    print(f"  Скачано: {downloaded_bytes/1e9:.2f} GB")
    print(f"  Время: {(time.time()-t0)/60:.1f} мин")
    print(f"  Манифест: {FILL_MANIFEST_PATH}")
    if failed_count:
        print("\n  ВНИМАНИЕ: часть fill-продуктов не скачалась — build_mosaic_2025.py")
        print("  должен работать и с частичным fill_manifest (compositing best-effort).")


if __name__ == "__main__":
    main()
