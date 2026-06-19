"""Генерация web-превью 2K. Запусти вручную если shell заблокирован."""
from PIL import Image
from pathlib import Path

dest = Path(__file__).resolve().parents[2] / "data" / "web"
dest.mkdir(exist_ok=True)

for name in ["ndvi", "ndwi"]:
    src = Path(__file__).resolve().parents[2] / "data" / "processed" / "preview" / f"{name}_tko_final.png"
    img = Image.open(src)
    w, h = img.size
    ratio = 2000 / max(w, h)
    img = img.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)
    out = dest / f"{name}_preview_2k.png"
    img.save(out)
    print(f"{name}: {w}x{h} → {out}")
print("Done")
