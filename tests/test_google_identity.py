import unittest
from unittest.mock import ANY, patch

from backend.google_identity import GOOGLE_CLOCK_SKEW_SECONDS, verify_google_id_token


class GoogleIdentityTests(unittest.TestCase):
    @patch("google.auth.transport.requests.Request")
    @patch("google.oauth2.id_token.verify_oauth2_token")
    def test_verifier_allows_small_clock_skew(self, verify_token, request_factory):
        request_factory.return_value = object()
        verify_token.return_value = {
            "iss": "https://accounts.google.com",
            "sub": "google-subject",
        }

        claims = verify_google_id_token("credential", "web-client-id")

        self.assertEqual(claims["sub"], "google-subject")
        verify_token.assert_called_once_with(
            "credential",
            ANY,
            "web-client-id",
            clock_skew_in_seconds=GOOGLE_CLOCK_SKEW_SECONDS,
        )
        self.assertEqual(GOOGLE_CLOCK_SKEW_SECONDS, 60)


if __name__ == "__main__":
    unittest.main()
