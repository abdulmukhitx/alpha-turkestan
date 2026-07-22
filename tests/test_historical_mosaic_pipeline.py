import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from src.processing import historical_mosaic_pipeline as pipeline


class HistoricalMosaicPipelineTests(unittest.TestCase):
    def test_cog_activity_detects_cpu_io_and_file_progress(self):
        base = pipeline.CogActivitySnapshot(0.0, 10.0, 100, 200, 300, 400)
        idle = pipeline.CogActivitySnapshot(60.0, 10.1, 100, 200, 300, 400)
        cpu = pipeline.CogActivitySnapshot(60.0, 10.6, 100, 200, 300, 400)
        io = pipeline.CogActivitySnapshot(
            60.0,
            10.1,
            100 + pipeline.COG_ACTIVITY_IO_THRESHOLD,
            200,
            300,
            400,
        )
        output = pipeline.CogActivitySnapshot(60.0, 10.1, 100, 200, 301, 400)
        temporary = pipeline.CogActivitySnapshot(60.0, 10.1, 100, 200, 300, 401)

        self.assertFalse(pipeline.cog_activity_advanced(base, idle))
        self.assertTrue(pipeline.cog_activity_advanced(base, cpu))
        self.assertTrue(pipeline.cog_activity_advanced(base, io))
        self.assertTrue(pipeline.cog_activity_advanced(base, output))
        self.assertTrue(pipeline.cog_activity_advanced(base, temporary))

    def test_systemd_service_retries_the_first_incomplete_year(self):
        unit = (pipeline.PROJECT_ROOT / "deploy/ubuntu/geoai-s2-history.service").read_text()
        self.assertNotIn("--keep-going", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("StartLimitIntervalSec=0", unit)
        self.assertIn("--gdal-threads 2", unit)
        self.assertIn("--cog-stall-minutes 45", unit)
        self.assertIn("MemoryHigh=5G", unit)
        self.assertIn("MemoryMax=6G", unit)
        self.assertIn("MemorySwapMax=2G", unit)

    def test_baseline_fallback_applies_offset_only_from_0400(self):
        self.assertEqual(
            pipeline.fallback_reflectance_recipe(
                "S2A_MSIL2A_20210701T000000_N0301_R000_T41ABC_20210701T000000"
            ),
            (0.0001, 0.0, "product_id_processing_baseline"),
        )
        self.assertEqual(
            pipeline.fallback_reflectance_recipe(
                "S2A_MSIL2A_20220701T000000_N0400_R000_T41ABC_20220701T000000"
            ),
            (0.0001, -0.1, "product_id_processing_baseline"),
        )

    def test_stac_asset_recipe_is_authoritative(self):
        self.assertEqual(
            pipeline.asset_recipe(
                {"raster:bands": [{"scale": 0.0002, "offset": -0.2}]},
                "S2A_MSIL2A_20210701T000000_N0301_R000_T41ABC_20210701T000000",
                None,
            ),
            (0.0002, -0.2, "stac_raster_bands"),
        )

    def test_orbit_diverse_selection_does_not_take_three_from_best_orbit(self):
        candidates = [
            {"product_id": "a", "cloud_cover": 0.0, "relative_orbit": "1", "datetime": "1"},
            {"product_id": "b", "cloud_cover": 0.1, "relative_orbit": "1", "datetime": "2"},
            {"product_id": "c", "cloud_cover": 0.2, "relative_orbit": "1", "datetime": "3"},
            {"product_id": "d", "cloud_cover": 1.0, "relative_orbit": "2", "datetime": "4"},
        ]
        selected = pipeline.select_orbit_diverse(candidates, 3)
        self.assertEqual([item["product_id"] for item in selected], ["a", "d", "b"])
        self.assertEqual(len(candidates), 4)

    def test_gdal_cog_translation_has_layout_and_overviews(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = pipeline.YearPaths(2020, root)
            paths.mosaic_dir.mkdir(parents=True)
            fingerprint = "test-fingerprint"
            profile = {
                "driver": "GTiff",
                "dtype": "float32",
                "width": 1024,
                "height": 1024,
                "count": 7,
                "crs": CRS.from_epsg(32641),
                "transform": from_origin(300000, 4800000, 10, 10),
                "nodata": -9999.0,
                "tiled": True,
                "blockxsize": 512,
                "blockysize": 512,
            }
            with rasterio.open(paths.staging, "w", **profile) as dataset:
                dataset.write(np.full((7, 1024, 1024), 0.2, dtype=np.float32))
                dataset.update_tags(
                    PIPELINE_VERSION=pipeline.PIPELINE_VERSION,
                    SOURCE_FINGERPRINT=fingerprint,
                )
            config = pipeline.PipelineConfig(
                data_root=root,
                boundary_path=root / "unused.geojson",
                strict_cog=True,
            )
            pipeline.translate_to_cog(paths, config, fingerprint)
            structure = pipeline.quick_mosaic_structure(paths.cog, require_cog_layout=True)
            self.assertTrue(structure["passed"], structure)
            with rasterio.open(paths.cog) as dataset:
                self.assertEqual(dataset.tags().get("SOURCE_FINGERPRINT"), fingerprint)


if __name__ == "__main__":
    unittest.main()
