"""Test helpers: locally generated RSA keys that mint Cognito-shaped tokens.

For unit tests only. Nothing here talks to AWS; keys exist only in memory.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from schedence_auth.jwks import JwksCache
from schedence_auth.verifier import CognitoTokenVerifier, CognitoVerifierConfig

TEST_REGION = "ap-south-1"
TEST_POOL_ID = "ap-south-1_TestPool1"
TEST_CLIENT_ID = "testclientid0000000000000a"
TEST_DEVTOOLS_CLIENT_ID = "testdevtoolsclient00000000"


@dataclass
class SigningKey:
    kid: str
    private_key: rsa.RSAPrivateKey = field(repr=False)

    @classmethod
    def generate(cls, kid: str | None = None) -> SigningKey:
        return cls(kid or f"test-{uuid.uuid4().hex[:8]}", rsa.generate_private_key(public_exponent=65537, key_size=2048))

    def public_jwk(self) -> dict[str, Any]:
        jwk = RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        return {**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}


class TokenFactory:
    """Mints access/ID tokens signed by in-memory keys and serves them as a JWKS."""

    def __init__(self, *, region: str = TEST_REGION, user_pool_id: str = TEST_POOL_ID, client_id: str = TEST_CLIENT_ID) -> None:
        self.region = region
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.key = SigningKey.generate()
        self.published_keys: list[SigningKey] = [self.key]
        self.fetch_count = 0

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    async def fetch_jwks(self, url: str) -> dict[str, Any]:
        self.fetch_count += 1
        return {"keys": [k.public_jwk() for k in self.published_keys]}

    def verifier(self, *, client_ids: frozenset[str] | None = None, required_scopes: frozenset[str] = frozenset(),
                 leeway_seconds: int = 0) -> CognitoTokenVerifier:
        config = CognitoVerifierConfig(
            region=self.region,
            user_pool_id=self.user_pool_id,
            client_ids=client_ids or frozenset({self.client_id}),
            required_scopes=required_scopes,
            leeway_seconds=leeway_seconds,
        )
        return CognitoTokenVerifier(config, jwks=JwksCache(config.jwks_url, fetcher=self.fetch_jwks, min_refresh_interval_seconds=0))

    def access_token(self, sub: str | None = None, *, key: SigningKey | None = None, expires_in: int = 3600,
                     scope: str = "openid email profile", **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": sub or str(uuid.uuid4()),
            "iss": self.issuer,
            "client_id": self.client_id,
            "origin_jti": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "token_use": "access",
            "scope": scope,
            "auth_time": now,
            "exp": now + expires_in,
            "iat": now,
            "jti": str(uuid.uuid4()),
            "username": sub or "test-user",
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        signer = key or self.key
        return jwt.encode(claims, signer.private_key, algorithm="RS256", headers={"kid": signer.kid})

    def id_token(self, sub: str | None = None, **overrides: Any) -> str:
        now = int(time.time())
        claims = {
            "sub": sub or str(uuid.uuid4()),
            "iss": self.issuer,
            "aud": self.client_id,
            "token_use": "id",
            "email": "someone@example.edu",
            "email_verified": True,
            "auth_time": now,
            "exp": now + 3600,
            "iat": now,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.key.private_key, algorithm="RS256", headers={"kid": self.key.kid})
