"""
GeoAI-TKO · src/processing/download_s2_2023_cdse.py
=======================================================
ЭТАП 2 (2023_summer CDSE rebuild) — скачивание top-3 orbit-diverse
продуктов (primary/fill1/fill2) на каждый из 43 master tile, СРАЗУ как
единый набор (не primary-затем-fill-позже, как было в 2025 по историческим
причинам) — план уже отобран в Этапе 1 (composite_candidates.json).

Качает только нужные JP2-ассеты (B02,B03,B04,B05,B08,B8A,B11) в
D:\\data\\s2_2023_cdse_raw\\{tile}\\{primary|fill1|fill2}\\{band}.jp2.

Resume-логика: пропускает уже скачанные и прошедшие verify_raster файлы;
манифест пишется ПОСЛЕ КАЖДОГО тайла (не только в конце), чтобы обрыв
процесса не откатывал прогресс на весь запуск заново (Урок 7).

Полный манифест (Урок 9): на каждый tile/slot — product_id,
processing:baseline, relative_orbit, datetime, cloud_cover, source
("cdse_direct"), boa_add_offset (выведен из baseline через
build_mosaic_2025.baseline_offset — тот же метод, что и 2025, не
переизобретается), n_unique_orbits_in_top3.

Запуск (обычно через detached PowerShell Start-Process, см. Этап 2 план):
  python src/processing/download_s2_2023_cdse.py > D:\\data\\s2_2023_cdse_raw\\download.log 2>&1
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
import os

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_mosaic_2025 import baseline_offset  # noqa: E402 — reuse, do not reimplement

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
ITEM_URL_TMPL = "https://catalogue.dataspace.copernicus.eu/stac/collections/sentinel-2-l2a/items/{id}"

CANDIDATES_PATH = Path(r"D:\data\s2_2023_cdse_raw\composite_candidates.json")
RAW_DIR = Path(r"D:\data\s2_2023_cdse_raw")
MANIFEST_PATH = RAW_DIR / "manifest.json"

ASSET_TO_BAND = {
    "B02_10m": "B02", "B03_10m": "B03", "B04_10m": "B04", "B08_10m": "B08",
    "B05_20m": "B05", "B8A_20m": "B8A", "B11_20m": "B11",
}
MAX_RETRIES = 3
TOKEN_REFRESH_MARGIN_S = 180

# Accept-Encoding without "br" — this venv's brotlicffi decoder throws
# DecodeError on CDSE's brotli-compressed responses (hit in Этап 1).
NO_BROTLI_HEADERS = {"Accept-Encoding": "gzip, deflate"}

HIGH_PERFORMANCE_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


# ── Pre-flight checks ───────────────────────────────────────────────
def check_power_plan():
    import subprocess
    # Match on GUID, not the localized plan name (Урок 8) — the name
    # garbles through subprocess's console codepage on ru locale, the
    # GUID is pure ASCII and survives regardless of codepage.
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
                headers=NO_BROTLI_HEADERS, timeout=30,
            )
            r.raise_for_status()
            d = r.json()
            self.token = d["access_token"]
            self.expires_at = time.time() + d["expires_in"]
            print(f"[auth] Новый токен получен, истекает через {d['expires_in']}s")
        return self.token


def fetch_item_assets(product_id: str) -> dict:
    r = requests.get(ITEM_URL_TMPL.format(id=product_id), headers=NO_BROTLI_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["assets"]


def download_asset(url, dest, token_mgr, expected_size=None):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            token = token_mgr.get()
            headers = {"Authorization": f"Bearer {token}", **NO_BROTLI_HEADERS}
            r = requests.get(url, headers=headers, timeout=120, stream=True)
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


def verify_raster(path: Path) -> bool:
    """Open via rasterio to confirm the file isn't truncated/corrupt."""
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


def download_slot(tile: str, slot: str, cand: dict, dest_dir: Path, token_mgr: TokenManager) -> dict | None:
    """Downloads all 7 bands for one candidate (primary/fill1/fill2) into
    dest_dir. Returns the manifest entry dict on success, None on failure."""
    pid = cand["product_id"]
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        assets = fetch_item_assets(pid)
    except Exception as e:
        print(f"    ОШИБКА получения ассетов для {pid}: {e}")
        return None

    band_files = {}
    for asset_key, band_name in ASSET_TO_BAND.items():
        a = assets.get(asset_key)
        if a is None:
            print(f"    ОШИБКА: ассет {asset_key} отсутствует в {pid}")
            return None
        href = a["alternate"]["https"]["href"]
        expected_size = a.get("file:size")
        dest = dest_dir / f"{band_name}.jp2"

        if dest.exists() and expected_size and dest.stat().st_size == expected_size and verify_raster(dest):
            print(f"    {band_name}: уже скачан и валиден, пропускаю")
        else:
            try:
                download_asset(href, dest, token_mgr, expected_size)
            except Exception as e:
                print(f"    ОШИБКА скачивания {band_name}: {e}")
                return None
            if not verify_raster(dest):
                print(f"    ОШИБКА: {band_name} не прошёл проверку целостности после скачивания")
                return None

        band_files[band_name] = str(dest)

    offset = baseline_offset(pid)
    return {
        "slot": slot,
        "product_id": pid,
        "cloud_cover": cand["cloud_cover"],
        "datetime": cand["datetime"],
        "relative_orbit": cand["relative_orbit"],
        "processing_baseline": cand.get("processing_baseline"),
        "source": "cdse_direct",
        "boa_add_offset": offset,
        "bands": band_files,
    }


def main():
    t0 = time.time()
    print("=" * 70)
    print("  GeoAI-TKO: Этап 2 — скачивание Sentinel-2 L2A, лето 2023 (CDSE, top-3 orbit-diverse)")
    print("=" * 70)

    check_power_plan()
    user, pwd = check_credentials()
    token_mgr = TokenManager(user, pwd)
    token_mgr.get()  # fail fast if creds are wrong

    if not CANDIDATES_PATH.exists():
        print(f"ОШИБКА: {CANDIDATES_PATH} не найден. Сначала Этап 1.")
        sys.exit(1)
    plan = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))["tiles"]
    if len(plan) != 43:
        print(f"ОШИБКА: ожидалось 43 тайла в плане, найдено {len(plan)}")
        sys.exit(1)

    manifest = {"year": 2023, "source": "cdse_direct", "generated_at": None, "tiles": {}}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        print(f"[resume] Найден существующий манифест: {len(manifest['tiles'])} тайлов уже отмечены готовыми")

    total_slots = sum(len(entry["candidates"]) for entry in plan.values())
    done_slots_at_start = sum(
        1 + len(manifest["tiles"].get(t, {}).get("fills", []))
        for t in manifest["tiles"]
    )
    print(f"\nПлан: {total_slots} product-slots всего ({len(plan)} тайлов x до 3). "
          f"Уже готово (resume): {done_slots_at_start}")

    ok_tiles, failed_tiles = [], list(manifest["tiles"].keys())
    downloaded_slots = done_slots_at_start

    for i, (tile, entry) in enumerate(sorted(plan.items()), 1):
        candidates = entry["candidates"]
        n_unique_orbits = entry["n_unique_orbits_in_top3"]

        if tile in manifest["tiles"] and manifest["tiles"][tile].get("complete"):
            print(f"[{i}/{len(plan)}] Тайл {tile}: уже полностью готов (resume), пропускаю")
            ok_tiles.append(tile)
            continue

        print(f"\n[{i}/{len(plan)}] Тайл {tile} | {len(candidates)} кандидата, "
              f"n_unique_orbits_in_top3={n_unique_orbits}")

        primary_cand = candidates[0]
        primary_entry = download_slot(tile, "primary", primary_cand, RAW_DIR / tile / "primary", token_mgr)
        if primary_entry is None:
            print(f"    ОШИБКА: primary для {tile} не скачался — тайл провален")
            failed_tiles.append(tile)
            continue
        downloaded_slots += 1

        fills = []
        for slot_i, cand in enumerate(candidates[1:], 1):
            fill_entry = download_slot(tile, f"fill{slot_i}", cand, RAW_DIR / tile / f"fill{slot_i}", token_mgr)
            if fill_entry is not None:
                fills.append(fill_entry)
                downloaded_slots += 1
            else:
                print(f"    ВНИМАНИЕ: fill{slot_i} для {tile} не скачался — продолжаю без него")

        manifest["tiles"][tile] = {
            "primary": primary_entry,
            "fills": fills,
            "n_unique_orbits_in_top3": n_unique_orbits,
            "complete": True,
        }
        ok_tiles.append(tile)
        if tile in failed_tiles:
            failed_tiles.remove(tile)

        manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        elapsed = time.time() - t0
        print(f"    Тайл {tile} готов: primary + {len(fills)} fill. "
              f"Прогресс: {downloaded_slots}/{total_slots} слотов, {elapsed/60:.1f} мин прошло")

    print("\n" + "=" * 70)
    print("  ИТОГ Этапа 2")
    print("=" * 70)
    print(f"  Тайлов успешно: {len(ok_tiles)}/{len(plan)}")
    print(f"  Слотов скачано: {downloaded_slots}/{total_slots}")
    print(f"  Время: {(time.time()-t0)/60:.1f} мин")
    print(f"  Манифест: {MANIFEST_PATH}")

    failed_only_no_primary = [t for t in plan if t not in manifest["tiles"]]
    if failed_only_no_primary:
        print(f"\n  ОШИБКА: {len(failed_only_no_primary)} тайлов без primary: {failed_only_no_primary}")
        print("  НЕ переходи к Этапу 3 пока эти тайлы не будут докачаны — перезапусти этот скрипт (resume).")
        sys.exit(1)
    else:
        print("\n  Все 43 тайла имеют минимум primary. Готово к Этапу 3.")


if __name__ == "__main__":
    main()
