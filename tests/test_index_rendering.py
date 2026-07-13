import io
import unittest

import numpy as np
from PIL import Image

from backend.main import LAYERS, compute_index, render_index


class NdreRenderingTests(unittest.TestCase):
    def test_ndre_uses_b08_and_b05_in_the_expected_order(self):
        bands = np.zeros((7, 1, 1), dtype=np.float32)
        bands[3, 0, 0] = 0.20  # B05 red edge
        bands[4, 0, 0] = 0.40  # B08 near infrared

        actual = compute_index(bands, "ndre", {"storage": "reflectance"})

        self.assertAlmostEqual(float(actual[0, 0]), 1 / 3, places=6)

    def test_ndre_stretch_covers_the_measured_multiyear_distribution(self):
        # Approximate 2nd-98th percentile envelope measured from the configured
        # 2023, 2024, and 2025 Sentinel-2 mosaics.
        self.assertEqual(LAYERS["ndre"]["range"], (-0.03, 0.51))

    def test_typical_positive_ndre_values_do_not_all_render_as_maximum_green(self):
        cfg = LAYERS["ndre"]
        values = np.array([[0.06, 0.30, 0.51]], dtype=np.float32)
        mask = np.full(values.shape, 255, dtype=np.uint8)

        png = render_index(values, mask, cfg["cmap"], *cfg["range"])
        pixels = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))[0]

        self.assertNotEqual(tuple(pixels[0]), tuple(pixels[-1]))
        self.assertNotEqual(tuple(pixels[1]), tuple(pixels[-1]))


if __name__ == "__main__":
    unittest.main()
