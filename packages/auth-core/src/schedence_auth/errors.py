"""Exceptions raised by ``schedence_auth``.

Messages are fixed strings chosen by reason code. They never contain the
token, its claims, or any configuration value, so they are safe to log and
to return to API clients.
"""

from __future__ import annotations


class AuthCoreError(Exception):
    """Base class for errors raised by ``schedence_auth``."""


class ConfigurationError(AuthCoreError, ValueError):
    """Settings are missing or invalid. The message names settings, never their values."""


#: reason code -> safe description
_REASONS: dict[str, str] = {
    "malformed": "The access token is malformed.",
    "unsupported_algorithm": "The access token uses an unsupported signing algorithm.",
    "unknown_key": "The access token was signed by an unknown key.",
    "invalid_signature": "The access token signature is invalid.",
    "expired": "The access token has expired.",
    "not_yet_valid": "The access token is not valid yet.",
    "invalid_issuer": "The access token was issued by an untrusted issuer.",
    "missing_claim": "The access token is missing a required claim.",
    "wrong_token_use": "An access token is required (ID and refresh tokens are not accepted).",
    "invalid_client": "The access token was issued to an unrecognised app client.",
    "insufficient_scope": "The access token does not carry the required scopes.",
}


class TokenVerificationError(AuthCoreError):
    """The presented token must not be trusted.

    ``reason`` is a stable, machine-readable code (see ``_REASONS``) that is
    safe to log. ``insufficient_scope`` maps to HTTP 403; everything else to 401.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in _REASONS else "malformed"
        super().__init__(_REASONS[self.reason])

    @property
    def status_code(self) -> int:
        return 403 if self.reason == "insufficient_scope" else 401


class JwksUnavailableError(AuthCoreError):
    """No signing keys could be loaded, so no token can be verified right now (HTTP 503)."""

    def __init__(self) -> None:
        super().__init__("Token signing keys are temporarily unavailable.")
