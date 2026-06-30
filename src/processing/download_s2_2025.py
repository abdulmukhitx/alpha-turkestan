"""
GeoAI-TKO · src/processing/download_s2_2025.py
=======================================================
ЭТАП 4 — Скачивание Sentinel-2 L2A ассетов за лето 2025 (43 тайла).

Скачивает ТОЛЬКО нужные JP2-ассеты (B02,B03,B04,B05,B08,B8A,B11) лучшего
(минимальная облачность) продукта на каждый из 43 master tile, в
D:\\data\\s2_2025_raw\\{tile_id}\\{band}.jp2. Не трогает .SAFE архивы целиком.

Перед стартом:
  - проверяет план электропитания (должен быть "Высокая производительность" —
    Power Saver throttled background I/O в прошлый раз и вызвал 3-часовое
    зависание; см. lessons learned по 2023_summer)
  - проверяет наличие CDSE_USERNAME/CDSE_PASSWORD в .env (нужны для
    resource owner password grant — это единственный grant, который CDSE
    принимает для скачивания через Zipper/OData; client_credentials с
    зарегистрированным OAuth-клиентом даёт 401 Token audience not allowed)

Запуск (фоново, лог в файл):
  python src/processing/download_s2_2025.py > D:\\data\\s2_2025_raw\\download.log 2>&1
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

STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
COLLECTION = "sentinel-2-l2a"
CLOUD_COVER_MAX = 40
YEAR = 2025

REPORT_PATH = Path(r"D:\data\availability_report.json")
RAW_DIR = Path(r"D:\data\s2_2025_raw")
MANIFEST_PATH = RAW_DIR / "manifest.json"

# asset key -> output band filename (10m bands kept at native 10m, 20m bands
# kept at native 20m — upsampling to the common 10m grid happens in Stage 5
# during the single per-tile reproject pass, NOT here)
ASSET_TO_BAND = {
    "B02_10m": "B02", "B03_10m": "B03", "B04_10m": "B04", "B08_10m": "B08",
    "B05_20m": "B05", "B8A_20m": "B8A", "B11_20m": "B11",
}

AOI = {
    "type": "Polygon",
    "coordinates": [[
        [65.928955, 40.530502],
        [70.97168, 40.530502],
        [70.97168, 46.035109],
        [65.928955, 46.035109],
        [65.928955, 40.530502],
    ]],
}

MAX_RETRIES = 3
TOKEN_REFRESH_MARGIN_S = 180  # re-auth if less than this remains


# ── Pre-flight checks ───────────────────────────────────────────────
HIGH_PERFORMANCE_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def check_power_plan():
    import subprocess
    # Match on GUID, not the localized plan name — the name garbles through
    # subprocess's console codepage, but the GUID is pure ASCII and survives.
    out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True,
                          errors="ignore").stdout
    print(f"[preflight] powercfg /getactivescheme -> {out.strip()}")
    if HIGH_PERFORMANCE_GUID not in out.lower():
        print(f"[preflight] ОШИБКА: активная схема не High Performance ({HIGH_PERFORMANCE_GUID}).")
        print(f"            Переключи вручную: powercfg /setactive {HIGH_PERFORMANCE_GUID}")
        sys.exit(1)
    print("[preflight] OK — план питания 'Высокая производительность' активен.")


def check_credentials():
    user = os.environ.get("CDSE_USERNAME", "").strip()
    pwd = os.environ.get("CDSE_PASSWORD", "").strip()
    if not user or not pwd:
        print("[preflight] ОШИБКА: CDSE_USERNAME/CDSE_PASSWORD не заданы в .env. Останавливаюсь.")
        sys.exit(1)
    print(f"[preflight] CDSE credentials найдены (user={user}).")
    return user, pwd


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


# ── STAC: find best product per tile ────────────────────────────────
def fetch_summer(year: int) -> list[dict]:
    body = {
        "collections": [COLLECTION],
        "intersects": AOI,
        "datetime": f"{year}-06-01T00:00:00Z/{year}-08-31T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": CLOUD_COVER_MAX}},
        "limit": 200,
    }
    feats = []
    url = STAC_URL
    while True:
        r = requests.post(url, json=body, timeout=60)
        r.raise_for_status()
        d = r.json()
        feats.extend(d.get("features", []))
        nxt = next((l for l in d.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt["href"]
        body = nxt.get("body", body)
    return feats


def best_per_tile(master_tiles, feats):
    best = {}
    for f in feats:
        p = f["properties"]
        tile = (p.get("grid:code") or "").replace("MGRS-", "")
        if tile not in master_tiles:
            continue
        cc = p.get("eo:cloud_cover")
        if cc is None:
            continue
        cur = best.get(tile)
        if cur is None or cc < cur["cloud_cover"]:
            best[tile] = {"feature": f, "cloud_cover": cc}
    return best


# ── Download with retry ─────────────────────────────────────────────
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
    """Open via rasterio to confirm the file isn't truncated/corrupt."""
    try:
        import rasterio
        from rasterio.windows import Window
        with rasterio.open(path) as ds:
            if ds.width <= 0 or ds.height <= 0:
                return False
            # force a read of a small window to catch truncated JP2 codestreams
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
    print("  GeoAI-TKO: Этап 4 — скачивание Sentinel-2 L2A, лето 2025")
    print("=" * 70)

    check_power_plan()
    user, pwd = check_credentials()
    token_mgr = TokenManager(user, pwd)
    token_mgr.get()  # fail fast if creds are wrong

    if not REPORT_PATH.exists():
        print(f"ОШИБКА: {REPORT_PATH} не найден.")
        sys.exit(1)
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    master_tiles = set(report["master_tiles"])
    print(f"Master tile set: {len(master_tiles)} тайлов")

    print(f"\nЗапрашиваю STAC за лето {YEAR}...")
    feats = fetch_summer(YEAR)
    best = best_per_tile(master_tiles, feats)
    missing = sorted(master_tiles - set(best.keys()))
    if missing:
        print(f"ОШИБКА: для {len(missing)} тайлов нет продукта <{CLOUD_COVER_MAX}%: {missing}")
        print("Это расходится с Этапом 3 — останавливаюсь, не качаю неполный набор.")
        sys.exit(1)
    print(f"Лучшие продукты найдены для {len(best)}/{len(master_tiles)} тайлов.")

    total_expected_bytes = 0
    for tile in sorted(best):
        assets = best[tile]["feature"]["assets"]
        for key in ASSET_TO_BAND:
            a = assets.get(key)
            if a and a.get("file:size"):
                total_expected_bytes += a["file:size"]
    total_expected_gb = total_expected_bytes / 1e9
    print(f"Ожидаемый объём: {total_expected_gb:.2f} GB\n")

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {"year": YEAR, "generated_at": datetime.now(timezone.utc).isoformat(), "tiles": {}}
    downloaded_bytes = 0
    ok_tiles, failed_tiles = [], []

    for i, tile in enumerate(sorted(best), 1):
        info = best[tile]
        feature = info["feature"]
        product_id = feature["id"]
        cloud = info["cloud_cover"]
        assets = feature["assets"]

        tile_dir = RAW_DIR / tile
        tile_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{i}/{len(best)}] Тайл {tile} | продукт {product_id} | облачность {cloud:.2f}%")

        band_files = {}
        tile_ok = True
        for asset_key, band_name in ASSET_TO_BAND.items():
            a = assets.get(asset_key)
            if a is None:
                print(f"    ОШИБКА: ассет {asset_key} отсутствует в продукте {product_id}")
                tile_ok = False
                break
            href = a["alternate"]["https"]["href"]
            expected_size = a.get("file:size")
            dest = tile_dir / f"{band_name}.jp2"

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
            ok_tiles.append(tile)
            manifest["tiles"][tile] = {
                "product_id": product_id,
                "cloud_cover": round(cloud, 2),
                "datetime": feature["properties"].get("datetime"),
                "bands": band_files,
            }
        else:
            failed_tiles.append(tile)

        pct = downloaded_bytes / total_expected_bytes * 100 if total_expected_bytes else 0
        elapsed = time.time() - t0
        print(f"    Прогресс: {len(ok_tiles)+len(failed_tiles)}/{len(best)} тайлов, "
              f"{downloaded_bytes/1e9:.2f} GB из {total_expected_gb:.2f} GB ({pct:.1f}%), "
              f"{elapsed/60:.1f} мин прошло\n")

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 70)
    print("  ИТОГ Этапа 4")
    print("=" * 70)
    print(f"  Успешно: {len(ok_tiles)}/{len(best)}")
    print(f"  Скачано: {downloaded_bytes/1e9:.2f} GB")
    print(f"  Время: {(time.time()-t0)/60:.1f} мин")
    print(f"  Манифест: {MANIFEST_PATH}")

    if failed_tiles:
        print(f"\n  ОШИБКА: {len(failed_tiles)} тайлов не скачались: {failed_tiles}")
        print("  НЕ переходи к Этапу 5 пока эти тайлы не будут докачаны.")
        sys.exit(1)
    else:
        print("\n  Все 43 тайла скачаны и прошли проверку целостности. Готово к Этапу 5.")


if __name__ == "__main__":
    main()
