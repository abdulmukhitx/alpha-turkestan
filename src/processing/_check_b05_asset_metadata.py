import json
import requests

CDSE_ITEM_URL = "https://catalogue.dataspace.copernicus.eu/stac/collections/sentinel-2-l2a/items/{id}"

# a 2025 primary product actually used in the mosaic
CDSE_PRODUCT = "S2B_MSIL2A_20250809T062629_N0511_R077_T41TQE_20250809T083435"

print("=" * 70)
print("  CDSE (direct) — B05 asset metadata")
print("=" * 70)
r = requests.get(CDSE_ITEM_URL.format(id=CDSE_PRODUCT), timeout=30)
r.raise_for_status()
assets = r.json()["assets"]
b05 = assets.get("B05_20m")
print(json.dumps(b05, indent=2, ensure_ascii=False))

print("\n" + "=" * 70)
print("  Planetary Computer — B05 asset metadata (сопоставимый 2023 продукт)")
print("=" * 70)
try:
    from pystac_client import Client
    import planetary_computer as pc

    catalog = Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=pc.sign_inplace,
    )
    # search for any 2023 summer product over the same AOI to inspect its B05 asset def
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=[65.9, 40.5, 71.0, 46.0],
        datetime="2023-07-01/2023-07-31",
        query={"eo:cloud_cover": {"lt": 20}},
        limit=1,
    )
    items = list(search.items())
    if items:
        item = items[0]
        print(f"Продукт: {item.id}")
        b05_pc = item.assets.get("B05")
        if b05_pc:
            print(json.dumps({
                "href": b05_pc.href,
                "media_type": b05_pc.media_type,
                "roles": b05_pc.roles,
                "extra_fields": b05_pc.extra_fields,
            }, indent=2, ensure_ascii=False, default=str))
        else:
            print("Ассет 'B05' не найден, доступные ключи:", list(item.assets.keys()))
    else:
        print("Продукты не найдены в PC для этого поиска")
except Exception as e:
    print(f"Ошибка при запросе PC: {e}")
