"""Authenticated, cache-bounded CDSE Sentinel Hub rendering for small AOIs."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw


CDSE_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
CDSE_PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"
MAX_TOKEN_RESPONSE_BYTES = 1_000_000
MAX_IMAGE_RESPONSE_BYTES = 12_000_000
EVALSCRIPT_VERSION = "geoai-tko-v5"
SUPPORTED_LAYERS = {"rgb", "ndvi", "ndwi", "ndre", "ndmi", "bsi", "savi", "nbr"}


class SceneRenderError(RuntimeError):
    """A safe rendering failure that never includes credentials or upstream bodies."""


class _AuthenticationRejected(SceneRenderError):
    pass


TokenFetcher = Callable[[str, str, float], tuple[str, int]]
ProcessFetcher = Callable[[dict[str, Any], str, float], bytes]


def _default_token_fetcher(client_id: str, client_secret: str, timeout: float) -> tuple[str, int]:
    try:
        response = httpx.post(
            CDSE_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json", "User-Agent": "GeoAI-TKO/4.0"},
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
        )
        response.raise_for_status()
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise SceneRenderError("CDSE authentication is temporarily unavailable") from exc
    except httpx.HTTPStatusError as exc:
        raise SceneRenderError("CDSE rejected the configured OAuth client") from exc

    if len(response.content) > MAX_TOKEN_RESPONSE_BYTES:
        raise SceneRenderError("CDSE authentication returned an invalid response")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SceneRenderError("CDSE authentication returned an invalid response") from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    expires_in = payload.get("expires_in", 600) if isinstance(payload, dict) else 600
    if not isinstance(token, str) or not token or len(token) > 20_000:
        raise SceneRenderError("CDSE authentication returned no access token")
    try:
        lifetime = max(60, min(int(expires_in), 86_400))
    except (TypeError, ValueError):
        lifetime = 600
    return token, lifetime


def _default_process_fetcher(payload: dict[str, Any], token: str, timeout: float) -> bytes:
    try:
        response = httpx.post(
            CDSE_PROCESS_URL,
            json=payload,
            headers={
                "Accept": "image/png",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "GeoAI-TKO/4.0",
            },
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise SceneRenderError("CDSE imagery processing is temporarily unavailable") from exc

    if response.status_code in {401, 403}:
        raise _AuthenticationRejected("CDSE rejected the imagery access token")
    if response.status_code == 429:
        raise SceneRenderError("CDSE processing quota is busy; retry shortly")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SceneRenderError("CDSE could not render this scene and AOI") from exc
    if len(response.content) > MAX_IMAGE_RESPONSE_BYTES:
        raise SceneRenderError("CDSE rendered image exceeded the safety limit")
    content_type = response.headers.get("content-type", "").lower()
    if "image/png" not in content_type or not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise SceneRenderError("CDSE returned an invalid scene image")
    return response.content


def geometry_bounds(geometry: dict[str, Any]) -> list[float]:
    """Return [west, south, east, north] for an already validated GeoJSON polygon."""
    positions: list[list[float]] = []
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if geometry.get("type") == "Polygon" and isinstance(coordinates, list):
        positions = [position for ring in coordinates for position in ring]
    elif geometry.get("type") == "MultiPolygon" and isinstance(coordinates, list):
        positions = [position for polygon in coordinates for ring in polygon for position in ring]
    if not positions:
        raise SceneRenderError("The selected AOI has no polygon coordinates")
    west = min(float(position[0]) for position in positions)
    south = min(float(position[1]) for position in positions)
    east = max(float(position[0]) for position in positions)
    north = max(float(position[1]) for position in positions)
    if not all(math.isfinite(value) for value in (west, south, east, north)) or west >= east or south >= north:
        raise SceneRenderError("The selected AOI has invalid polygon bounds")
    return [west, south, east, north]


def approximate_bbox_area_km2(bounds: list[float]) -> float:
    west, south, east, north = bounds
    mid_latitude = math.radians((south + north) / 2)
    width_km = (east - west) * 111.32 * max(0.01, math.cos(mid_latitude))
    height_km = (north - south) * 110.57
    return abs(width_km * height_km)


def _output_dimensions(bounds: list[float], max_dimension: int) -> tuple[int, int]:
    west, south, east, north = bounds
    mid_latitude = math.radians((south + north) / 2)
    width = max((east - west) * math.cos(mid_latitude), 1e-8)
    height = max(north - south, 1e-8)
    if width >= height:
        return max_dimension, max(256, round(max_dimension * height / width))
    return max(256, round(max_dimension * width / height)), max_dimension


def _aoi_coverage_percent(
    content: bytes,
    geometry: dict[str, Any],
    bounds: list[float],
    width: int,
    height: int,
) -> float:
    """Measure Process API dataMask coverage inside the requested AOI."""
    try:
        with Image.open(BytesIO(content)) as source:
            source.load()
            if source.size != (width, height):
                raise SceneRenderError("CDSE returned an unexpected scene image size")
            alpha = source.convert("RGBA").getchannel("A")
    except SceneRenderError:
        raise
    except Exception as exc:
        raise SceneRenderError("CDSE returned an invalid scene image") from exc

    west, south, east, north = bounds
    expected = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(expected)

    def pixel(position: list[float]) -> tuple[float, float]:
        return (
            (float(position[0]) - west) / (east - west) * (width - 1),
            (north - float(position[1])) / (north - south) * (height - 1),
        )

    coordinates = geometry.get("coordinates") or []
    polygons = [coordinates] if geometry.get("type") == "Polygon" else coordinates
    for polygon in polygons:
        if not polygon:
            continue
        draw.polygon([pixel(position) for position in polygon[0]], fill=255)
        for hole in polygon[1:]:
            draw.polygon([pixel(position) for position in hole], fill=0)

    expected_count = expected.histogram()[255]
    if expected_count <= 0:
        raise SceneRenderError("The selected AOI cannot be rasterized")
    valid_alpha = alpha.point(lambda value: 255 if value > 0 else 0)
    valid_count = valid_alpha.histogram(mask=expected)[255]
    return round(valid_count / expected_count * 100, 2)


_INDEX_CONFIG = {
    "ndvi": (["B08", "B04"], "(s.B08-s.B04)/(s.B08+s.B04)", 0.02, 0.21, "rdylgn"),
    "ndwi": (["B03", "B08"], "(s.B03-s.B08)/(s.B03+s.B08)", -0.27, -0.10, "rdbu"),
    "ndre": (["B08", "B05"], "(s.B08-s.B05)/(s.B08+s.B05)", -0.03, 0.51, "rdylgn"),
    "ndmi": (["B08", "B11"], "(s.B08-s.B11)/(s.B08+s.B11)", -0.20, 0.16, "rdbu"),
    "bsi": (["B11", "B04", "B08", "B02"], "((s.B11+s.B04)-(s.B08+s.B02))/((s.B11+s.B04)+(s.B08+s.B02))", 0.12, 0.29, "oranges"),
    "savi": (["B08", "B04"], "1.5*(s.B08-s.B04)/(s.B08+s.B04+0.5)", -0.10, 0.35, "rdylgn"),
    "nbr": (["B08", "B11"], "(s.B08-s.B11)/(s.B08+s.B11)", -0.22, 0.29, "rdylgn"),
}

_PALETTES = {
    "rdylgn": [[165, 0, 38], [253, 174, 97], [255, 255, 191], [166, 217, 106], [0, 104, 55]],
    "rdbu": [[103, 0, 31], [214, 96, 77], [247, 247, 247], [146, 197, 222], [5, 48, 97]],
    "oranges": [[255, 245, 235], [253, 208, 162], [253, 141, 60], [217, 71, 1], [127, 39, 4]],
}


def _evalscript(layer: str) -> str:
    if layer == "rgb":
        return f"""//VERSION=3
function setup() {{ return {{input:[\"B02\",\"B03\",\"B04\",\"dataMask\"],output:{{bands:4}}}}; }}
function evaluatePixel(s) {{
  return [Math.min(1,2.5*s.B04),Math.min(1,2.5*s.B03),Math.min(1,2.5*s.B02),s.dataMask];
}}"""

    bands, expression, minimum, maximum, palette_name = _INDEX_CONFIG[layer]
    palette = json.dumps(_PALETTES[palette_name], separators=(",", ":"))
    inputs = json.dumps([*bands, "SCL", "dataMask"], separators=(",", ":"))
    return f"""//VERSION=3
const palette={palette};
function setup() {{ return {{input:{inputs},output:{{bands:4}}}}; }}
function ramp(value) {{
  const scaled=Math.max(0,Math.min(1,(value-({minimum}))/(({maximum})-({minimum}))))*(palette.length-1);
  const left=Math.floor(scaled), right=Math.min(palette.length-1,left+1), mix=scaled-left;
  return [0,1,2].map(i=>(palette[left][i]+(palette[right][i]-palette[left][i])*mix)/255);
}}
function qualityColour(s) {{
  if (s.SCL===3) return [0.14,0.17,0.21,1];
  if ([8,9,10].includes(s.SCL)) return [0.86,0.89,0.93,1];
  if (s.SCL===11) return [0.78,0.92,1,1];
  if ([0,1].includes(s.SCL)) return [0.38,0.38,0.38,1];
  return null;
}}
function evaluatePixel(s) {{
  if (s.dataMask!==1) return [0,0,0,0];
  const quality = qualityColour(s);
  if (quality) return quality;
  const denominatorSafe = Object.keys(s).every(key => Number.isFinite(s[key]));
  const value = denominatorSafe ? {expression} : 0;
  const rgb = ramp(Number.isFinite(value)?value:0);
  return [rgb[0],rgb[1],rgb[2],1];
}}"""


class CdseSceneRenderer:
    """Render and cache scene-sized PNGs without exposing OAuth credentials."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        cache_dir: Path,
        timeout_seconds: float = 45.0,
        cache_max_bytes: int = 1_073_741_824,
        max_dimension: int = 768,
        token_fetcher: TokenFetcher | None = None,
        process_fetcher: ProcessFetcher | None = None,
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.enabled = bool(self.client_id and self.client_secret)
        self.cache_dir = Path(cache_dir)
        self.timeout_seconds = max(5.0, min(float(timeout_seconds), 120.0))
        self.cache_max_bytes = max(16_000_000, min(int(cache_max_bytes), 20_000_000_000))
        self.max_dimension = max(256, min(int(max_dimension), 1024))
        self._token_fetcher = token_fetcher or _default_token_fetcher
        self._process_fetcher = process_fetcher or _default_process_fetcher
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    def capabilities(self) -> dict[str, Any]:
        return {
            "scene_rendering": self.enabled,
            "scene_export": False,
            "quality_mask": "Sentinel-2 L2A SCL",
            "render_layers": sorted(SUPPORTED_LAYERS),
            "max_frame_dimension": self.max_dimension,
            "note": (
                "Catalogue scenes can be rendered and cached for a small AOI."
                if self.enabled else
                "Set CDSE_CLIENT_ID and CDSE_CLIENT_SECRET on the backend to render scenes."
            ),
        }

    def _access_token(self, *, force_refresh: bool = False) -> str:
        if not self.enabled:
            raise SceneRenderError("CDSE scene rendering is not configured")
        now = time.monotonic()
        with self._lock:
            if not force_refresh and self._token and now < self._token_expires_at:
                return self._token
            token, lifetime = self._token_fetcher(
                self.client_id, self.client_secret, self.timeout_seconds,
            )
            self._token = token
            self._token_expires_at = now + max(30, lifetime - 45)
            return token

    def _cache_path(self, payload: dict[str, Any]) -> Path:
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self.cache_dir / f"{digest}.png"

    def _read_cache(self, path: Path) -> bytes | None:
        try:
            content = path.read_bytes()
            if content.startswith(b"\x89PNG\r\n\x1a\n"):
                path.touch(exist_ok=True)
                return content
        except OSError:
            return None
        return None

    def _write_cache(self, path: Path, content: bytes) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{threading.get_ident()}.tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
            files = sorted(self.cache_dir.glob("*.png"), key=lambda item: item.stat().st_mtime)
            total = sum(item.stat().st_size for item in files)
            for item in files:
                if total <= self.cache_max_bytes:
                    break
                size = item.stat().st_size
                item.unlink(missing_ok=True)
                total -= size
        except OSError:
            # Caching is an optimization; a valid rendered frame should still be served.
            return

    def render(
        self,
        *,
        geometry: dict[str, Any],
        acquired_at: datetime,
        layer: str,
    ) -> dict[str, Any]:
        if layer not in SUPPORTED_LAYERS:
            raise SceneRenderError("This layer is not supported for CDSE timelapse rendering")
        bounds = geometry_bounds(geometry)
        width, height = _output_dimensions(bounds, self.max_dimension)
        if acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(tzinfo=timezone.utc)
        acquired_at = acquired_at.astimezone(timezone.utc)
        # The catalogue exposes MGRS granules while the UI exposes one AOI frame
        # per UTC day. A full-day interval lets Process API stitch every tile
        # available for that daily acquisition before coverage is measured.
        start = acquired_at.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1) - timedelta(microseconds=1)
        timestamp = acquired_at.date().isoformat()
        cache_identity = {
            "version": EVALSCRIPT_VERSION,
            "geometry": geometry,
            "acquired_at": timestamp,
            "layer": layer,
            "width": width,
            "height": height,
        }
        cache_path = self._cache_path(cache_identity)
        cached = self._read_cache(cache_path)
        if cached is not None:
            coverage_percent = _aoi_coverage_percent(cached, geometry, bounds, width, height)
            return {
                "content": cached, "cached": True, "width": width, "height": height,
                "coverage_percent": coverage_percent,
            }

        payload = {
            "input": {
                "bounds": {
                    "bbox": bounds,
                    "geometry": geometry,
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
                },
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {
                            "from": start.isoformat().replace("+00:00", "Z"),
                            "to": end.isoformat().replace("+00:00", "Z"),
                        },
                        "mosaickingOrder": "leastCC",
                    },
                    "processing": {"upsampling": "BICUBIC", "downsampling": "BILINEAR"},
                }],
            },
            "output": {
                "width": width,
                "height": height,
                "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
            },
            "evalscript": _evalscript(layer),
        }
        token = self._access_token()
        try:
            content = self._process_fetcher(payload, token, self.timeout_seconds)
        except _AuthenticationRejected:
            token = self._access_token(force_refresh=True)
            content = self._process_fetcher(payload, token, self.timeout_seconds)
        coverage_percent = _aoi_coverage_percent(content, geometry, bounds, width, height)
        self._write_cache(cache_path, content)
        return {
            "content": content, "cached": False, "width": width, "height": height,
            "coverage_percent": coverage_percent,
        }
