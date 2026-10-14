"""Cached JSON Web Key Set (JWKS) for one Cognito user pool.

Keys are cached by ``kid``. The cache refreshes when it expires (TTL) and when
a token presents a ``kid`` it has not seen, which is how Cognito signing-key
rotation is picked up. Refreshes are rate-limited so that tokens with random
``kid`` values cannot make the service hammer the JWKS endpoint. If a refresh
fails, previously loaded keys keep working (stale-if-error).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jwt

from schedence_auth.errors import JwksUnavailableError, TokenVerificationError

logger = logging.getLogger(__name__)

JwksFetcher = Callable[[str], Awaitable[dict[str, Any]]]


async def fetch_jwks_over_https(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Download a JWKS document. Redirects are not followed."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()


class JwksCache:
    def __init__(
        self,
        jwks_url: str,
        *,
        fetcher: JwksFetcher | None = None,
        ttl_seconds: float = 3600,
        min_refresh_interval_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = jwks_url
        self._fetcher = fetcher or fetch_jwks_over_https
        self._ttl = ttl_seconds
        self._min_interval = min_refresh_interval_seconds
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] | None = None
        self._expires_at = 0.0
        self._last_attempt: float | None = None
        self._lock = asyncio.Lock()

    @property
    def key_ids(self) -> frozenset[str]:
        return frozenset(self._keys or ())

    async def get_key(self, kid: str) -> jwt.PyJWK:
        """Return the public key for ``kid``.

        Raises:
            TokenVerificationError: ``unknown_key`` if no such key exists after a refresh.
            JwksUnavailableError: if no keys have ever been loaded.
        """
        if self._is_fresh() and kid in self._keys:  # type: ignore[operator]
            return self._keys[kid]  # type: ignore[index]

        async with self._lock:
            if not (self._is_fresh() and kid in self._keys) and self._may_refresh():  # type: ignore[operator]
                await self._refresh()

        if self._keys is None:
            raise JwksUnavailableError()
        key = self._keys.get(kid)
        if key is None:
            raise TokenVerificationError("unknown_key")
        return key

    async def refresh(self) -> None:
        """Force a refresh (still subject to the minimum interval)."""
        async with self._lock:
            if self._may_refresh():
                await self._refresh()

    def _is_fresh(self) -> bool:
        return self._keys is not None and self._clock() < self._expires_at

    def _may_refresh(self) -> bool:
        return self._last_attempt is None or self._clock() - self._last_attempt >= self._min_interval

    async def _refresh(self) -> None:
        self._last_attempt = self._clock()
        try:
            document = await self._fetcher(self._url)
        except Exception as exc:  # network, HTTP status, JSON: keep any stale keys
            logger.warning("JWKS refresh failed (%s); %s", type(exc).__name__,
                           "keeping cached keys" if self._keys else "no keys available")
            return

        keys: dict[str, jwt.PyJWK] = {}
        for entry in document.get("keys", []) if isinstance(document, dict) else []:
            if not isinstance(entry, dict):
                continue
            kid = entry.get("kid")
            if (
                not isinstance(kid, str)
                or not kid
                or entry.get("kty") != "RSA"
                or entry.get("use", "sig") != "sig"
                or entry.get("alg", "RS256") != "RS256"
            ):
                continue
            try:
                keys[kid] = jwt.PyJWK(entry, algorithm="RS256")
            except jwt.PyJWKError:
                continue

        if not keys:
            logger.warning("JWKS document contained no usable RS256 signing keys")
            return
        self._keys = keys
        self._expires_at = self._clock() + self._ttl
        logger.info("Loaded %d JWKS signing key(s)", len(keys))
