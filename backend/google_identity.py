"""Verification for Google Identity Services ID tokens.

The browser sends only the short-lived credential returned by Google's button.
No Google access or refresh tokens are stored by this application.
"""

from __future__ import annotations


class GoogleIdentityDependencyError(RuntimeError):
    """Raised when Google login is configured without its verifier dependency."""


GOOGLE_CLOCK_SKEW_SECONDS = 60


def verify_google_id_token(credential: str, client_id: str) -> dict:
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover - depends on deployment packaging
        raise GoogleIdentityDependencyError(
            "Install the google-auth package to enable Google sign-in"
        ) from exc

    claims = id_token.verify_oauth2_token(
        credential,
        GoogleRequest(),
        client_id,
        clock_skew_in_seconds=GOOGLE_CLOCK_SKEW_SECONDS,
    )
    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("unexpected Google token issuer")
    return claims
