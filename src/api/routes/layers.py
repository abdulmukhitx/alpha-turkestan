"""
GET /layers        — список всех слоёв
GET /layers/{name} — метаданные конкретного слоя
GET /metadata      — сводка по всем слоям (format: list)
"""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["layers"])

METADATA_DIR = Path(__file__).resolve().parents[3] / "data" / "metadata"

_layer_cache: dict | None = None


def _load_all() -> dict:
    global _layer_cache
    if _layer_cache is None:
        _layer_cache = {}
        for f in METADATA_DIR.glob("*.json"):
            data = json.loads(f.read_text())
            _layer_cache[data["layer"]] = data
    return _layer_cache


@router.get("/metadata")
def get_metadata():
    """Сводка по всем слоям."""
    return [
        {
            "name": v["name"],
            "layer": v["layer"],
            "year": 2023,
            "resolution": v["resolution_m"],
            "crs": v["crs"],
            "value_range": v.get("value_range"),
        }
        for v in _load_all().values()
    ]


@router.get("/layers")
def list_layers():
    """Все доступные слои (без деталей)."""
    return [
        {"name": v["layer"], "resolution": v["resolution_m"]}
        for v in _load_all().values()
    ]


@router.get("/layers/{name}")
def get_layer(name: str):
    """Метаданные одного слоя."""
    layers = _load_all()
    if name not in layers:
        raise HTTPException(404, f"Layer '{name}' not found. Available: {list(layers.keys())}")
    return layers[name]
