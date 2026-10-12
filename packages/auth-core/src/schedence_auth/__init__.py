"""AWS Cognito access-token verification for Schedence services.

This package answers one question: *is this a genuine, unexpired access token
issued by our user pool to one of our app clients?* It does not decide what the
caller may do; services resolve roles and permissions from their own data.
"""

import logging

from schedence_auth.claims import AccessTokenClaims
from schedence_auth.errors import AuthCoreError, ConfigurationError, JwksUnavailableError, TokenVerificationError
from schedence_auth.jwks import JwksCache, fetch_jwks_over_https
from schedence_auth.settings import CognitoSettings, build_verifier, load_settings_model
from schedence_auth.verifier import CognitoTokenVerifier, CognitoVerifierConfig, cognito_issuer, cognito_jwks_url

__version__ = "0.1.0"

__all__ = [
    "AccessTokenClaims",
    "AuthCoreError",
    "CognitoSettings",
    "CognitoTokenVerifier",
    "CognitoVerifierConfig",
    "ConfigurationError",
    "JwksCache",
    "JwksUnavailableError",
    "TokenVerificationError",
    "build_verifier",
    "cognito_issuer",
    "cognito_jwks_url",
    "fetch_jwks_over_https",
    "load_settings_model",
]

logging.getLogger(__name__).addHandler(logging.NullHandler())
