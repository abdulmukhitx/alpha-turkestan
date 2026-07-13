import unittest

import numpy as np

from backend.main import linear_trend_array, linear_trend_summary


class LinearForecastTests(unittest.TestCase):
    def test_exact_linear_history_projects_next_year(self):
        actual = linear_trend_summary(
            [2023, 2024, 2025], [0.10, 0.20, 0.30], 2026, "ndvi",
        )

        self.assertEqual(actual["predicted"], 0.40)
        self.assertEqual(actual["direction"], "improving")
        self.assertEqual(actual["trend_quality"], "consistent")
        self.assertEqual(actual["r_squared"], 1.0)

    def test_bsi_increase_is_degradation(self):
        actual = linear_trend_summary(
            [2023, 2024, 2025], [0.10, 0.20, 0.30], 2026, "bsi",
        )

        self.assertEqual(actual["direction"], "degrading")

    def test_raster_forecast_runs_along_time_axis(self):
        years = np.array([2023, 2024, 2025], dtype=np.float32)
        values = np.array([
            [[0.10, 0.30]],
            [[0.20, 0.20]],
            [[0.30, 0.10]],
        ], dtype=np.float32)

        actual = linear_trend_array(years, values, 2026)

        np.testing.assert_allclose(actual, [[0.40, 0.00]], atol=1e-6)


if __name__ == "__main__":
    unittest.main()
