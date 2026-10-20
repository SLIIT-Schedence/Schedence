"""Identity permission lookups under a burst of parallel reads (a page that loads many resources at once)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.auth.errors import ServiceUnavailableError
from app.auth.identity_client import IdentityPermissionsClient

INSTITUTION = uuid.UUID("11111111-1111-4111-8111-111111111111")
SUB = "exam-admin-sub"
CLAIMS = SimpleNamespace(sub=SUB, expires_at=datetime.now(UTC) + timedelta(hours=1))


class SlowIdentity:
    """Answers like the Identity Service, slowly, and counts the calls it receives."""

    def __init__(self, *, delay: float = 0.05, status: int = 200, answer_sub: str = SUB) -> None:
        self.calls = 0
        self.delay, self.status, self.answer_sub = delay, status, answer_sub

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": "X", "message": "x"}})
        return httpx.Response(200, json={
            "user_id": str(uuid.uuid4()), "cognito_sub": self.answer_sub, "account_status": "ACTIVE",
            "institutions": [{"institution_id": str(INSTITUTION), "roles": ["EXAMINATION_ADMIN"], "permissions": ["examinations:read"]}],
        })


def client_for(identity: SlowIdentity) -> IdentityPermissionsClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(identity.handler))
    return IdentityPermissionsClient("http://identity.test", http=http, cache_seconds=15)


async def test_parallel_reads_share_one_identity_lookup() -> None:
    identity = SlowIdentity()
    client = client_for(identity)
    principals = await asyncio.gather(*(client.principal_for("token", CLAIMS) for _ in range(22)))
    assert identity.calls == 1
    assert {p.user_id for p in principals} == {principals[0].user_id}
    assert principals[0].permissions_by_institution[INSTITUTION] == frozenset({"examinations:read"})
    await client.principal_for("token", CLAIMS)  # now cached
    assert identity.calls == 1 and not client._lookups


async def test_each_token_gets_its_own_lookup() -> None:
    identity = SlowIdentity()
    client = client_for(identity)
    await asyncio.gather(client.principal_for("token-a", CLAIMS), client.principal_for("token-a", CLAIMS),
                         client.principal_for("token-b", CLAIMS))
    assert identity.calls == 2


async def test_writes_always_ask_identity_again() -> None:
    identity = SlowIdentity()
    client = client_for(identity)
    await asyncio.gather(*(client.principal_for("token", CLAIMS, fresh=True) for _ in range(3)))
    assert identity.calls == 3
    await client.principal_for("token", CLAIMS, fresh=True)  # even with a warm cache
    assert identity.calls == 4


@pytest.mark.parametrize("identity", [SlowIdentity(status=500), SlowIdentity(answer_sub="someone-else")])
async def test_every_waiter_fails_closed_and_the_next_request_retries(identity: SlowIdentity) -> None:
    client = client_for(identity)
    results = await asyncio.gather(*(client.principal_for("token", CLAIMS) for _ in range(5)), return_exceptions=True)
    assert all(isinstance(r, ServiceUnavailableError) for r in results)
    assert identity.calls == 1 and not client._lookups
    identity.status, identity.answer_sub = 200, SUB
    assert (await client.principal_for("token", CLAIMS)).cognito_sub == SUB  # a failure is never cached
    assert identity.calls == 2


async def test_a_timeout_fails_closed_for_every_waiter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = IdentityPermissionsClient("http://identity.test", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    results = await asyncio.gather(*(client.principal_for("token", CLAIMS) for _ in range(4)), return_exceptions=True)
    assert all(isinstance(r, ServiceUnavailableError) for r in results)


async def test_one_caller_giving_up_does_not_break_the_others() -> None:
    identity = SlowIdentity(delay=0.1)
    client = client_for(identity)
    impatient = asyncio.create_task(client.principal_for("token", CLAIMS))
    patient = asyncio.create_task(client.principal_for("token", CLAIMS))
    await asyncio.sleep(0.01)
    impatient.cancel()
    assert (await patient).cognito_sub == SUB
    assert identity.calls == 1
