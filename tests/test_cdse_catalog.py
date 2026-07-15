import unittest

from backend.cdse_catalog import CdseSceneCatalog, SceneSearchError


class CdseSceneCatalogTests(unittest.TestCase):
    def test_search_builds_bounded_query_and_normalises_scenes(self):
        calls = []

        def fetcher(url, payload, timeout):
            calls.append((url, payload, timeout))
            return {
                "type": "FeatureCollection",
                "context": {"matched": 17},
                "features": [
                    {
                        "id": "S2B_TEST_SCENE",
                        "collection": "sentinel-2-l2a",
                        "bbox": [68.0, 42.0, 68.2, 42.2],
                        "properties": {
                            "datetime": "2025-06-03T06:12:00Z",
                            "eo:cloud_cover": 4.257,
                            "platform": "sentinel-2b",
                            "s2:mgrs_tile": "42TXN",
                        },
                        "links": [{"rel": "self", "href": "https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-l2a/items/test"}],
                    },
                    {"id": "missing-date", "properties": {}},
                ],
            }

        catalog = CdseSceneCatalog(fetcher=fetcher, timeout_seconds=7, cache_seconds=300)
        result = catalog.search(
            bbox=[68.0, 42.0, 69.0, 43.0],
            start_date="2025-06-01",
            end_date="2025-08-31",
            max_cloud_cover=20,
            limit=30,
        )

        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["matched"], 17)
        self.assertFalse(result["cached"])
        self.assertFalse(result["scenes"][0]["renderable"])
        self.assertEqual(result["scenes"][0]["cloud_cover"], 4.26)
        self.assertEqual(calls[0][0], "https://stac.dataspace.copernicus.eu/v1/search")
        self.assertEqual(calls[0][1]["collections"], ["sentinel-2-l2a"])
        self.assertEqual(calls[0][1]["query"], {"eo:cloud_cover": {"lte": 20}})
        self.assertEqual(calls[0][2], 7)

    def test_identical_search_uses_cache(self):
        calls = 0

        def fetcher(_url, _payload, _timeout):
            nonlocal calls
            calls += 1
            return {"type": "FeatureCollection", "features": []}

        catalog = CdseSceneCatalog(fetcher=fetcher, cache_seconds=60)
        query = dict(
            bbox=[68.0, 42.0, 69.0, 43.0], start_date="2025-01-01",
            end_date="2025-12-31", max_cloud_cover=30, limit=20,
        )
        self.assertFalse(catalog.search(**query)["cached"])
        self.assertTrue(catalog.search(**query)["cached"])
        self.assertEqual(calls, 1)

    def test_invalid_feature_collection_is_rejected(self):
        catalog = CdseSceneCatalog(fetcher=lambda *_args: {"features": "invalid"})
        with self.assertRaises(SceneSearchError):
            catalog.search(
                bbox=[68.0, 42.0, 69.0, 43.0], start_date="2025-01-01",
                end_date="2025-12-31", max_cloud_cover=30, limit=20,
            )


if __name__ == "__main__":
    unittest.main()
