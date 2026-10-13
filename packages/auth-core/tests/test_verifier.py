from __future__ import annotations

import base64
import json
import time

import jwt
import pytest

from schedence_auth import ConfigurationError, CognitoVerifierConfig, JwksUnavailableError, TokenVerificationError
from schedence_auth.testing import TEST_CLIENT_ID, TEST_POOL_ID, SigningKey, TokenFactory


async def _reason(verifier, token: str) -> str:
    with pytest.raises(TokenVerificationError) as info:
        await verifier.verify_access_token(token)
    return info.value.reason


async def test_valid_access_token_returns_claims(tokens: TokenFactory) -> None:
    claims = await tokens.verifier().verify_access_token(tokens.access_token("user-sub-1"))
    assert claims.sub == "user-sub-1"
    assert claims.client_id == TEST_CLIENT_ID
    assert claims.scopes == {"openid", "email", "profile"}
    assert claims.expires_at > claims.issued_at


async def test_tampered_payload_fails_signature_check(tokens: TokenFactory) -> None:
    header, payload, signature = tokens.access_token("victim").split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    data["sub"] = "attacker"
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    assert await _reason(tokens.verifier(), f"{header}.{forged}.{signature}") == "invalid_signature"


async def test_expired_token_is_rejected(tokens: TokenFactory) -> None:
    token = tokens.access_token(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
    assert await _reason(tokens.verifier(), token) == "expired"


async def test_token_from_another_user_pool_issuer_is_rejected(tokens: TokenFactory) -> None:
    token = tokens.access_token(iss="https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_OtherPool")
    assert await _reason(tokens.verifier(), token) == "invalid_issuer"


async def test_token_signed_by_another_pools_key_is_rejected(tokens: TokenFactory) -> None:
    other_pool_key = SigningKey.generate("other-pool-key")
    assert await _reason(tokens.verifier(), tokens.access_token(key=other_pool_key)) == "unknown_key"


async def test_incorrect_app_client_is_rejected(tokens: TokenFactory) -> None:
    token = tokens.access_token(client_id="someotherclientid000000000")
    assert await _reason(tokens.verifier(), token) == "invalid_client"


async def test_id_token_is_rejected_for_api_authorization(tokens: TokenFactory) -> None:
    assert await _reason(tokens.verifier(), tokens.id_token()) == "wrong_token_use"


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
async def test_non_rs256_algorithms_are_rejected(tokens: TokenFactory, algorithm: str) -> None:
    claims = {"sub": "x", "iss": tokens.issuer, "token_use": "access", "client_id": TEST_CLIENT_ID,
              "exp": int(time.time()) + 60, "iat": int(time.time())}
    key = None if algorithm == "none" else "a-shared-secret-that-is-long-enough-for-hs256"
    token = jwt.encode(claims, key, algorithm=algorithm, headers={"kid": tokens.key.kid})
    assert await _reason(tokens.verifier(), token) == "unsupported_algorithm"


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.b.c.d", "x" * 20_000])
async def test_malformed_tokens_are_rejected(tokens: TokenFactory, token: str) -> None:
    assert await _reason(tokens.verifier(), token) == "malformed"


async def test_missing_required_claim_is_rejected(tokens: TokenFactory) -> None:
    assert await _reason(tokens.verifier(), tokens.access_token(token_use=None)) == "missing_claim"


async def test_required_scope_is_enforced(tokens: TokenFactory) -> None:
    verifier = tokens.verifier(required_scopes=frozenset({"openid", "schedence/api"}))
    with pytest.raises(TokenVerificationError) as info:
        await verifier.verify_access_token(tokens.access_token(scope="openid email"))
    assert info.value.reason == "insufficient_scope"
    assert info.value.status_code == 403


async def test_signing_key_rotation_is_picked_up(tokens: TokenFactory) -> None:
    verifier = tokens.verifier()
    await verifier.verify_access_token(tokens.access_token())  # caches the original key
    new_key = SigningKey.generate("rotated-key")
    tokens.published_keys.append(new_key)  # Cognito publishes a new key
    claims = await verifier.verify_access_token(tokens.access_token("after-rotation", key=new_key))
    assert claims.sub == "after-rotation"
    assert tokens.fetch_count == 2


async def test_unknown_kid_refetch_is_rate_limited(tokens: TokenFactory) -> None:
    from schedence_auth import JwksCache

    clock = [1000.0]
    config = CognitoVerifierConfig(region=tokens.region, user_pool_id=tokens.user_pool_id, client_ids=frozenset({TEST_CLIENT_ID}))
    jwks = JwksCache(config.jwks_url, fetcher=tokens.fetch_jwks, min_refresh_interval_seconds=30, clock=lambda: clock[0])
    from schedence_auth import CognitoTokenVerifier

    verifier = CognitoTokenVerifier(config, jwks=jwks)
    await verifier.verify_access_token(tokens.access_token())
    clock[0] += 31  # past the minimum interval: the first unknown kid may trigger one refresh
    for i in range(5):
        await _reason(verifier, tokens.access_token(key=SigningKey.generate(f"random-{i}")))
    assert tokens.fetch_count == 2  # initial load + one refresh; the rest were rate-limited


async def test_jwks_failure_without_cached_keys_is_unavailable(tokens: TokenFactory) -> None:
    async def broken(url: str) -> dict:
        raise ConnectionError("network down")

    from schedence_auth import CognitoTokenVerifier, JwksCache

    config = CognitoVerifierConfig(region=tokens.region, user_pool_id=tokens.user_pool_id, client_ids=frozenset({TEST_CLIENT_ID}))
    verifier = CognitoTokenVerifier(config, jwks=JwksCache(config.jwks_url, fetcher=broken))
    with pytest.raises(JwksUnavailableError):
        await verifier.verify_access_token(tokens.access_token())


async def test_jwks_failure_keeps_stale_keys(tokens: TokenFactory) -> None:
    from schedence_auth import CognitoTokenVerifier, JwksCache

    clock = [0.0]
    healthy = [True]

    async def flaky(url: str) -> dict:
        if not healthy[0]:
            raise ConnectionError("network down")
        return await tokens.fetch_jwks(url)

    config = CognitoVerifierConfig(region=tokens.region, user_pool_id=tokens.user_pool_id, client_ids=frozenset({TEST_CLIENT_ID}))
    verifier = CognitoTokenVerifier(config, jwks=JwksCache(config.jwks_url, fetcher=flaky, ttl_seconds=60,
                                                           min_refresh_interval_seconds=0, clock=lambda: clock[0]))
    await verifier.verify_access_token(tokens.access_token())
    healthy[0], clock[0] = False, 120.0  # cache expired and the endpoint is down
    assert (await verifier.verify_access_token(tokens.access_token("still-ok"))).sub == "still-ok"


def test_config_rejects_pool_from_other_region() -> None:
    with pytest.raises(ConfigurationError):
        CognitoVerifierConfig(region="us-east-1", user_pool_id=TEST_POOL_ID, client_ids=frozenset({TEST_CLIENT_ID}))


def test_error_messages_never_contain_the_token(tokens: TokenFactory) -> None:
    error = TokenVerificationError("invalid_signature")
    assert "eyJ" not in str(error)
