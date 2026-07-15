import unittest

from backend.rate_limit import SlidingWindowRateLimiter


class SlidingWindowRateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.limiter = SlidingWindowRateLimiter(clock=lambda: self.now)

    def test_rejects_after_limit_and_reports_retry_after(self):
        self.assertEqual(
            self.limiter.consume("client", limit=2, window_seconds=60),
            (True, 1, 0),
        )
        self.assertEqual(
            self.limiter.consume("client", limit=2, window_seconds=60),
            (True, 0, 0),
        )
        self.now = 110.2
        self.assertEqual(
            self.limiter.consume("client", limit=2, window_seconds=60),
            (False, 0, 50),
        )

    def test_bucket_recovers_when_window_expires(self):
        self.limiter.consume("client", limit=1, window_seconds=10)
        self.now = 110.0
        self.assertEqual(
            self.limiter.consume("client", limit=1, window_seconds=10),
            (True, 0, 0),
        )

    def test_keys_have_independent_quotas(self):
        self.limiter.consume("first", limit=1, window_seconds=60)
        self.assertTrue(
            self.limiter.consume("second", limit=1, window_seconds=60)[0]
        )


if __name__ == "__main__":
    unittest.main()
