import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.cdse_process import CdseSceneRenderer, approximate_bbox_area_km2, geometry_bounds


POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [68.20, 42.50], [68.24, 42.50], [68.24, 42.53], [68.20, 42.50],
    ]],
}
PNG = b"\x89PNG\r\n\x1a\nmock-frame"


class CdseSceneRendererTests(unittest.TestCase):
    def test_render_uses_exact_geometry_scl_and_disk_cache(self):
        token_calls = []
        process_calls = []

        def token_fetcher(client_id, client_secret, timeout):
            token_calls.append((client_id, client_secret, timeout))
            return "access-token", 600

        def process_fetcher(payload, token, timeout):
            process_calls.append((payload, token, timeout))
            return PNG

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
        self.assertEqual(first["content"], PNG)
        self.assertEqual(len(token_calls), 1)
        self.assertEqual(len(process_calls), 1)
        payload, token, _timeout = process_calls[0]
        self.assertEqual(token, "access-token")
        self.assertEqual(payload["input"]["bounds"]["geometry"], POLYGON)
        self.assertEqual(payload["input"]["data"][0]["type"], "sentinel-2-l2a")
        self.assertIn("SCL", payload["evalscript"])
        self.assertIn("B08", payload["evalscript"])
        self.assertLessEqual(payload["output"]["width"], 640)
        self.assertLessEqual(payload["output"]["height"], 640)

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
