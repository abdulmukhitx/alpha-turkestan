import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main


class StabilityContractTests(unittest.TestCase):
    def test_public_health_does_not_disclose_filesystem_paths(self):
        with TestClient(main.app) as client:
            payload = client.get("/health").json()
            self.assertNotIn("cog_path", payload)
            self.assertNotIn("s2_dir", payload)
            self.assertIn(payload["status"], {"ok", "degraded"})

            liveness = client.get("/healthz")
            self.assertEqual(liveness.status_code, 200)
            self.assertEqual(liveness.json()["status"], "ok")

    def test_expensive_routes_receive_the_stricter_quota(self):
        self.assertEqual(
            main._rate_limit_policy("/api/zone_stats"),
            ("analysis", main.ANALYSIS_RATE_LIMIT),
        )
        self.assertEqual(
            main._rate_limit_policy("/api/periods"),
            ("api", main.API_RATE_LIMIT),
        )
        self.assertEqual(
            main._rate_limit_policy("/api/timelapse/scenes"),
            ("analysis", main.ANALYSIS_RATE_LIMIT),
        )

    def test_scene_search_rejects_invalid_bounds_before_network_access(self):
        request = main.TimelapseSceneSearchReq(
            bbox=[70, 42, 68, 43],
            start_date="2025-01-01",
            end_date="2025-12-31",
            max_cloud_cover=20,
            limit=10,
        )
        with self.assertRaises(main.HTTPException) as raised:
            main.validate_scene_search(request)
        self.assertEqual(raised.exception.status_code, 400)

    @patch.object(main.CDSE_SCENE_CATALOG, "search")
    def test_scene_search_endpoint_returns_catalogue_only_results(self, search):
        search.return_value = {
            "scenes": [{
                "scene_id": "S2C_TEST",
                "acquired_at": "2025-06-02T06:16:51Z",
                "cloud_cover": 3.2,
                "renderable": False,
            }],
            "returned": 1,
            "matched": None,
            "cached": False,
        }
        with patch.object(main, "CDSE_CATALOG_ENABLED", True), TestClient(main.app) as client:
            response = client.post("/api/timelapse/scenes", json={
                "bbox": [68.2, 42.45, 68.35, 42.6],
                "start_date": "2025-06-01",
                "end_date": "2025-06-30",
                "max_cloud_cover": 20,
                "limit": 10,
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["returned"], 1)
        self.assertFalse(response.json()["scenes"][0]["renderable"])
        search.assert_called_once_with(
            bbox=[68.2, 42.45, 68.35, 42.6],
            start_date="2025-06-01",
            end_date="2025-06-30",
            max_cloud_cover=20.0,
            limit=10,
        )

    def test_historical_tiles_are_cacheable_and_versioned(self):
        headers = main.tile_cache_headers(main.PERIODS[main.DEFAULT_PERIOD])
        self.assertIn("max-age=", headers["Cache-Control"])
        self.assertIn("stale-while-revalidate", headers["Cache-Control"])
        self.assertTrue(headers["ETag"].startswith('"'))
        self.assertTrue(headers["X-Data-Version"])

    @patch("backend.main.OpenAI")
    def test_ai_client_has_bounded_timeout_and_retries(self, openai):
        main.ai_client("test-key", "https://example.test/v1")
        openai.assert_called_once_with(
            api_key="test-key",
            base_url="https://example.test/v1",
            timeout=main.AI_TIMEOUT_SECONDS,
            max_retries=main.AI_MAX_RETRIES,
        )


if __name__ == "__main__":
    unittest.main()
