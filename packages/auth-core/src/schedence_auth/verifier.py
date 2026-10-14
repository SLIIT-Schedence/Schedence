"""Verification of Amazon Cognito user pool **access** tokens.

Checks, in order (see AWS "Verifying JSON web tokens"):

1. shape and size of the compact JWT;
2. header ``alg`` is exactly ``RS256`` (no ``none``, no HMAC key confusion) and ``kid`` is present;
3. RS256 signature against the user pool JWKS key with that ``kid``;
4. ``iss`` equals ``https://cognito-idp.<region>.amazonaws.com/<userPoolId>``;
5. ``exp`` / ``iat`` (with a small leeway for clock skew);
6. ``token_use`` is ``access`` (ID tokens are rejected);
7. ``client_id`` is one of the configured app clients (access tokens carry
   ``client_id``, not ``aud``);
8. required OAuth scopes, if configured.

Only after all of this are the claims returned. Authorisation (roles and
permissions) is deliberately *not* done here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import jwt

from schedence_auth.claims import AccessTokenClaims
from schedence_auth.errors import ConfigurationError, TokenVerificationError
from schedence_auth.jwks import JwksCache

REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d+$")
USER_POOL_ID_PATTERN = re.compile(r"^(?P<region>[a-z]{2}(?:-[a-z]+)+-\d+)_[0-9A-Za-z]+$")
CLIENT_ID_PATTERN = re.compile(r"^[\w+]{1,128}$")
ALGORITHM = "RS256"
_REQUIRED_CLAIMS = ["exp", "iat", "iss", "sub", "token_use"]


def cognito_issuer(region: str, user_pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"


def cognito_jwks_url(issuer: str) -> str:
    return f"{issuer}/.well-known/jwks.json"


@dataclass(frozen=True)
class CognitoVerifierConfig:
    region: str
    user_pool_id: str
    client_ids: frozenset[str]
    required_scopes: frozenset[str] = field(default_factory=frozenset)
    leeway_seconds: int = 30
    max_token_length: int = 16_384

    def __post_init__(self) -> None:
        if not REGION_PATTERN.match(self.region):
            raise ConfigurationError("AWS_REGION is not a valid AWS Region code")
        match = USER_POOL_ID_PATTERN.match(self.user_pool_id)
        if not match:
            raise ConfigurationError("COGNITO_USER_POOL_ID is not a valid user pool ID")
        if match.group("region") != self.region:
            raise ConfigurationError("COGNITO_USER_POOL_ID belongs to a different Region than AWS_REGION")
        if not self.client_ids or not all(CLIENT_ID_PATTERN.match(c) for c in self.client_ids):
            raise ConfigurationError("COGNITO_APP_CLIENT_ID is missing or invalid")

    @property
    def issuer(self) -> str:
        return cognito_issuer(self.region, self.user_pool_id)

    @property
    def jwks_url(self) -> str:
        return cognito_jwks_url(self.issuer)


class CognitoTokenVerifier:
    def __init__(self, config: CognitoVerifierConfig, *, jwks: JwksCache | None = None) -> None:
        self.config = config
        self.jwks = jwks or JwksCache(config.jwks_url)

    @property
    def issuer(self) -> str:
        return self.config.issuer

    async def verify_access_token(self, token: str) -> AccessTokenClaims:
        """Return the verified claims, or raise :class:`TokenVerificationError`."""
        if not isinstance(token, str) or not token or len(token) > self.config.max_token_length or token.count(".") != 2:
            raise TokenVerificationError("malformed")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise TokenVerificationError("malformed") from None
        if header.get("alg") != ALGORITHM:
            raise TokenVerificationError("unsupported_algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise TokenVerificationError("malformed")

        key = await self.jwks.get_key(kid)
        payload = self._decode(token, key)

        if payload.get("token_use") != "access":
            raise TokenVerificationError("wrong_token_use")
        client_id = payload.get("client_id")
        if not isinstance(client_id, str):
            raise TokenVerificationError("missing_claim")
        if client_id not in self.config.client_ids:
            raise TokenVerificationError("invalid_client")
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise TokenVerificationError("missing_claim")

        scope_claim = payload.get("scope", "")
        scopes = frozenset(scope_claim.split()) if isinstance(scope_claim, str) else frozenset()
        if not self.config.required_scopes <= scopes:
            raise TokenVerificationError("insufficient_scope")

        return AccessTokenClaims(
            sub=sub,
            client_id=client_id,
            username=payload.get("username") if isinstance(payload.get("username"), str) else None,
            scopes=scopes,
            issued_at=_timestamp(payload["iat"]),
            expires_at=_timestamp(payload["exp"]),
            token_id=_optional_str(payload.get("jti")),
            origin_token_id=_optional_str(payload.get("origin_jti")),
            auth_time=_timestamp(payload["auth_time"]) if isinstance(payload.get("auth_time"), int | float) else None,
        )

    def _decode(self, token: str, key: jwt.PyJWK) -> dict[str, Any]:
        try:
            return jwt.decode(
                token,
                key=key.key,
                algorithms=[ALGORITHM],
                issuer=self.config.issuer,
                leeway=self.config.leeway_seconds,
                options={"require": _REQUIRED_CLAIMS, "verify_aud": False},
            )
        except jwt.ExpiredSignatureError:
            raise TokenVerificationError("expired") from None
        except jwt.InvalidIssuerError:
            raise TokenVerificationError("invalid_issuer") from None
        except jwt.InvalidSignatureError:
            raise TokenVerificationError("invalid_signature") from None
        except jwt.MissingRequiredClaimError:
            raise TokenVerificationError("missing_claim") from None
        except (jwt.ImmatureSignatureError, jwt.InvalidIssuedAtError):
            raise TokenVerificationError("not_yet_valid") from None
        except jwt.InvalidAlgorithmError:
            raise TokenVerificationError("unsupported_algorithm") from None
        except jwt.PyJWTError:
            raise TokenVerificationError("malformed") from None


def _timestamp(value: Any) -> datetime:
    return datetime.fromtimestamp(float(value), tz=UTC)


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
