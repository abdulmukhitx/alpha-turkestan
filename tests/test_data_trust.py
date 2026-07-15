import io
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from backend.main import period_evidence, render_true_color


class DataTrustTests(unittest.TestCase):
    def setUp(self):
        self.period = {
            "date_range": "01.06.2025 – 31.08.2025",
            "storage": "reflectance",
            "cog_path": Path("missing-test-mosaic.tif"),
        }

    def test_evidence_discloses_missing_cloud_mask_without_paths(self):
        evidence = period_evidence("2025_summer", self.period)
        self.assertEqual(evidence["quality"]["mask_type"], "nodata_only")
        self.assertFalse(evidence["quality"]["cloud_mask_applied"])
        self.assertEqual(evidence["provenance_completeness"], "partial")
        self.assertNotIn("cog_path", evidence)
        self.assertNotIn("missing-test-mosaic", str(evidence))

    def test_true_color_uses_red_green_blue_order_and_alpha_mask(self):
        data = np.zeros((7, 2, 2), dtype=np.float32)
        data[0] = 0.04
        data[1] = 0.08
        data[2] = 0.16
        mask = np.array([[255, 0], [255, 255]], dtype=np.uint8)
        content = render_true_color(data, mask, self.period)
        rgba = np.asarray(Image.open(io.BytesIO(content)).convert("RGBA"))
        self.assertGreater(rgba[0, 0, 0], rgba[0, 0, 1])
        self.assertGreater(rgba[0, 0, 1], rgba[0, 0, 2])
        self.assertEqual(rgba[0, 1, 3], 0)
        self.assertEqual(rgba[1, 1, 3], 255)


if __name__ == "__main__":
    unittest.main()
