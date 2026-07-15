"""Bounded Sentinel-2 scene discovery through the public CDSE STAC API."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx


CDSE_STAC_URL = "https://stac.dataspace.copernicus.eu/v1"
CDSE_COLLECTION = "sentinel-2-l2a"
MAX_RESPONSE_BYTES = 2_000_000


class SceneSearchError(RuntimeError):
    """A safe, user-facing catalogue failure without upstream response details."""


Fetcher = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _default_fetcher(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    try:
        response = httpx.post(
            url,
            json=payload,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            headers={
                "Accept": "application/geo+json, application/json",
                "Content-Type": "application/json",
                "User-Agent": "GeoAI-TKO/4.0",
            },
        )
        response.raise_for_status()
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise SceneSearchError("CDSE catalogue is temporarily unavailable") from exc
    except httpx.HTTPStatusError as exc:
        raise SceneSearchError("CDSE catalogue rejected the scene search") from exc

    if len(response.content) > MAX_RESPONSE_BYTES:
        raise SceneSearchError("CDSE catalogue response exceeded the safety limit")
    try:
        data = response.json()
    except ValueError as exc:
        raise SceneSearchError("CDSE catalogue returned an invalid response") from exc
    if not isinstance(data, dict):
        raise SceneSearchError("CDSE catalogue returned an invalid response")
    return data


def _safe_number(value: Any, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if minimum <= number <= maximum else None


def _safe_item_url(feature: dict[str, Any]) -> str | None:
    for link in feature.get("links") or []:
        if not isinstance(link, dict) or link.get("rel") != "self":
            continue
        href = str(link.get("href") or "")
        parsed = urlparse(href)
        if parsed.scheme == "https" and parsed.hostname == "stac.dataspace.copernicus.eu":
            return href[:1000]
    return None


def _normalise_scene(feature: Any) -> dict[str, Any] | None:
    if not isinstance(feature, dict):
        return None
    scene_id = str(feature.get("id") or "").strip()
    properties = feature.get("properties")
    bbox = feature.get("bbox")
    if not scene_id or len(scene_id) > 240 or not isinstance(properties, dict):
        return None
    acquired_at = properties.get("datetime") or properties.get("start_datetime")
    if not isinstance(acquired_at, str) or len(acquired_at) > 64:
        return None

    safe_bbox = None
    if isinstance(bbox, list) and len(bbox) >= 4:
        values = [_safe_number(value, -180.0, 180.0) for value in bbox[:4]]
        if all(value is not None for value in values):
            safe_bbox = values

    cloud_cover = _safe_number(properties.get("eo:cloud_cover"), 0.0, 100.0)
    return {
        "scene_id": scene_id,
        "acquired_at": acquired_at,
        "cloud_cover": round(cloud_cover, 2) if cloud_cover is not None else None,
        "collection": str(feature.get("collection") or CDSE_COLLECTION)[:80],
        "platform": str(properties.get("platform") or properties.get("constellation") or "Sentinel-2")[:80],
        "mgrs_tile": str(properties.get("s2:mgrs_tile") or "")[:32] or None,
        "bbox": safe_bbox,
        "item_url": _safe_item_url(feature),
        "renderable": False,
        "source": "cdse_stac",
    }


class CdseSceneCatalog:
    """Small cached adapter around the official public STAC search endpoint."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout_seconds: float = 12.0,
        cache_seconds: int = 300,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))
        self.cache_seconds = max(0, min(int(cache_seconds), 3600))
        self._fetcher = fetcher or _default_fetcher
        self._cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def capabilities(self) -> dict[str, Any]:
        return {
            "catalogue_search": self.enabled,
            "scene_rendering": False,
            "scene_export": False,
            "collection": CDSE_COLLECTION,
            "provider": "Copernicus Data Space Ecosystem",
            "catalogue_url": CDSE_STAC_URL,
            "note": "Scene results are metadata only; playback still uses local annual mosaics.",
        }

    def search(
        self,
        *,
        bbox: list[float],
        start_date: str,
        end_date: str,
        max_cloud_cover: float,
        limit: int,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise SceneSearchError("CDSE scene catalogue is disabled")

        cache_key = (
            *(round(value, 5) for value in bbox),
            start_date,
            end_date,
            round(max_cloud_cover, 1),
            limit,
        )
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] <= self.cache_seconds:
                result = copy.deepcopy(cached[1])
                result["cached"] = True
                return result

        payload = {
            "collections": [CDSE_COLLECTION],
            "bbox": bbox,
            "datetime": f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lte": max_cloud_cover}},
            "sortby": [{"field": "properties.datetime", "direction": "asc"}],
            "limit": limit,
        }
        data = self._fetcher(f"{CDSE_STAC_URL}/search", payload, self.timeout_seconds)
        features = data.get("features")
        if not isinstance(features, list):
            raise SceneSearchError("CDSE catalogue returned an invalid feature collection")

        scenes = []
        seen_ids: set[str] = set()
        for feature in features[:limit]:
            scene = _normalise_scene(feature)
            if scene and scene["scene_id"] not in seen_ids:
                scenes.append(scene)
                seen_ids.add(scene["scene_id"])

        matched = None
        context = data.get("context")
        if isinstance(context, dict):
            matched_value = context.get("matched")
            if isinstance(matched_value, int) and matched_value >= 0:
                matched = matched_value
        result = {
            "scenes": scenes,
            "returned": len(scenes),
            "matched": matched,
            "cached": False,
        }
        with self._lock:
            if len(self._cache) >= 100:
                oldest_key = min(self._cache, key=lambda key: self._cache[key][0])
                self._cache.pop(oldest_key, None)
            self._cache[cache_key] = (now, copy.deepcopy(result))
        return result
