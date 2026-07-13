import unittest

import numpy as np

from backend.main import period_to_texture_reflectance


class ClassifierTextureScaleTests(unittest.TestCase):
    def test_reflectance_storage_is_not_scaled(self):
        reflectance = np.array([[[0.10, 0.12], [0.11, 0.09]]], dtype=np.float32)

        actual = period_to_texture_reflectance(reflectance, {"storage": "reflectance"})

        np.testing.assert_array_equal(actual, reflectance)

    def test_dn_and_reflectance_storage_produce_same_texture_scale(self):
        reflectance = np.array([[[0.10, 0.12], [0.11, 0.09]]], dtype=np.float32)
        dn = reflectance * 10_000

        from_reflectance = period_to_texture_reflectance(reflectance, {"storage": "reflectance"})
        from_dn = period_to_texture_reflectance(dn, {"storage": "dn"})

        np.testing.assert_allclose(from_dn, from_reflectance, rtol=0, atol=1e-7)
        np.testing.assert_allclose(from_dn.std(), from_reflectance.std(), rtol=0, atol=1e-7)


if __name__ == "__main__":
    unittest.main()
