import unittest

from backend.main import point_within_bounds


class PointBoundsTests(unittest.TestCase):
    def setUp(self):
        self.bounds = [39.3309, 65.2579, 47.2279, 72.5162]

    def test_accepts_northern_and_eastern_coverage(self):
        self.assertTrue(point_within_bounds(47.1, 68.2, self.bounds))
        self.assertTrue(point_within_bounds(43.2, 72.2, self.bounds))

    def test_accepts_southern_coverage(self):
        self.assertTrue(point_within_bounds(39.8, 68.2, self.bounds))

    def test_rejects_points_outside_coverage(self):
        self.assertFalse(point_within_bounds(48.0, 68.2, self.bounds))
        self.assertFalse(point_within_bounds(43.2, 73.0, self.bounds))


if __name__ == "__main__":
    unittest.main()
