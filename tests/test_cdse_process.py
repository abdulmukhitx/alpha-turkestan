import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

from backend.cdse_process import CdseSceneRenderer, approximate_bbox_area_km2, geometry_bounds


POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [68.20, 42.50], [68.24, 42.50], [68.24, 42.53], [68.20, 42.50],
    ]],
}


def frame_png(payload, *, valid_fraction=1.0):
    width = payload["output"]["width"]
    height = payload["output"]["height"]
    image = Image.new("RGBA", (width, height), (40, 120, 60, 0))
    opaque_width = round(width * valid_fraction)
    if opaque_width:
        image.paste((40, 120, 60, 255), (0, 0, opaque_width, height))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class CdseSceneRendererTests(unittest.TestCase):
    def test_render_uses_exact_geometry_scl_and_disk_cache(self):
        token_calls = []
        process_calls = []

        def token_fetcher(client_id, client_secret, timeout):
            token_calls.append((client_id, client_secret, timeout))
            return "access-token", 600

        def process_fetcher(payload, token, timeout):
            process_calls.append((payload, token, timeout))
            return frame_png(payload)

        with tempfile.TemporaryDirectory() as directory:
            renderer = CdseSceneRenderer(
                client_id="client-id",
                client_secret="client-secret",
                cache_dir=Path(directory),
                max_dimension=640,
                token_fetcher=token_fetcher,
                process_fetcher=process_fetcher,
            )
            request = dict(
                geometry=POLYGON,
                acquired_at=datetime(2025, 6, 3, 6, 12, tzinfo=timezone.utc),
                layer="ndvi",
            )
            first = renderer.render(**request)
            second = renderer.render(**request)

        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertTrue(first["content"].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(first["coverage_percent"], 100.0)
        self.assertEqual(second["coverage_percent"], 100.0)
        self.assertEqual(len(token_calls), 1)
        self.assertEqual(len(process_calls), 1)
        payload, token, _timeout = process_calls[0]
        self.assertEqual(token, "access-token")
        self.assertEqual(payload["input"]["bounds"]["geometry"], POLYGON)
        self.assertEqual(payload["input"]["data"][0]["type"], "sentinel-2-l2a")
        self.assertIn("SCL", payload["evalscript"])
        self.assertIn("B08", payload["evalscript"])
        self.assertIn("s.dataMask!==1", payload["evalscript"])
        self.assertNotIn("clear?1:0", payload["evalscript"])
        time_range = payload["input"]["data"][0]["dataFilter"]["timeRange"]
        self.assertEqual(time_range["from"], "2025-06-03T00:00:00Z")
        self.assertTrue(time_range["to"].startswith("2025-06-03T23:59:59"))
        self.assertLessEqual(payload["output"]["width"], 640)
        self.assertLessEqual(payload["output"]["height"], 640)

    def test_render_reports_partial_aoi_coverage(self):
        def process_fetcher(payload, _token, _timeout):
            return frame_png(payload, valid_fraction=0.25)

        with tempfile.TemporaryDirectory() as directory:
            renderer = CdseSceneRenderer(
                client_id="client-id", client_secret="client-secret", cache_dir=Path(directory),
                token_fetcher=lambda *_args: ("access-token", 600),
                process_fetcher=process_fetcher,
            )
            rendered = renderer.render(
                geometry=POLYGON,
                acquired_at=datetime(2025, 6, 3, 6, 12, tzinfo=timezone.utc),
                layer="ndvi",
            )
        self.assertGreater(rendered["coverage_percent"], 0)
        self.assertLess(rendered["coverage_percent"], 50)

    def test_capabilities_are_disabled_without_oauth_client(self):
        renderer = CdseSceneRenderer(
            client_id="", client_secret="", cache_dir=Path("unused"),
        )
        self.assertFalse(renderer.enabled)
        self.assertFalse(renderer.capabilities()["scene_rendering"])

    def test_geometry_area_is_positive_and_bounded(self):
        bounds = geometry_bounds(POLYGON)
        self.assertEqual(bounds, [68.2, 42.5, 68.24, 42.53])
        self.assertGreater(approximate_bbox_area_km2(bounds), 0)


if __name__ == "__main__":
    unittest.main()
