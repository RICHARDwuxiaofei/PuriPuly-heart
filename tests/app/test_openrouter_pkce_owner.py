from __future__ import annotations

import asyncio

import pytest

from puripuly_heart.app.services.openrouter_pkce_owner import (
    CancelOpenRouterPkce,
    OpenRouterPkceOwner,
    ReopenOpenRouterPkce,
    StartOpenRouterPkce,
)
from puripuly_heart.core.messages import (
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
    TransactionResult,
)
from puripuly_heart.core.openrouter_pkce import OpenRouterPKCEExchangeResult


class Client:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.cancelled = 0
        self.reopened = 0

    async def run_desktop_flow(self) -> OpenRouterPKCEExchangeResult:
        await self.release.wait()
        return OpenRouterPKCEExchangeResult("secret", None)

    def reopen_authorization_url(self) -> bool:
        self.reopened += 1
        return True

    def cancel(self) -> None:
        self.cancelled += 1
        self.release.set()


class Handoff:
    def __init__(
        self,
        status: str = TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    ) -> None:
        self.requests = []
        self.status = status

    async def complete_handoff(self, request):  # noqa: ANN001, ANN201
        self.requests.append(request)
        return TransactionResult(self.status, None, None)


def command() -> StartOpenRouterPkce:
    return StartOpenRouterPkce({"intent": {}}, "r1", "letter", "corr")


@pytest.mark.asyncio
async def test_single_active_flow_reopens_then_completes_typed_handoff() -> None:
    client = Client()
    handoff = Handoff()
    owner = OpenRouterPkceOwner(client_factory=lambda: client, handoff=handoff)  # type: ignore[arg-type]
    active = asyncio.create_task(owner.execute(command()))
    await asyncio.sleep(0)

    reopened = await owner.execute(command())
    assert reopened.status == "reopened"
    assert client.reopened == 1
    client.release.set()
    result = await active

    assert result.status == "succeeded"
    assert handoff.requests[0].reason == "openrouter_pkce"
    assert handoff.requests[0].expected_settings_revision == "r1"
    assert "transient_api_key='secret'" not in repr(handoff.requests[0])


@pytest.mark.asyncio
async def test_cancel_and_shutdown_are_idempotent_and_close_active_flow() -> None:
    client = Client()
    owner = OpenRouterPkceOwner(client_factory=lambda: client, handoff=Handoff())  # type: ignore[arg-type]
    active = asyncio.create_task(owner.execute(command()))
    await asyncio.sleep(0)

    assert (await owner.execute(ReopenOpenRouterPkce())).status == "reopened"
    assert (await owner.execute(CancelOpenRouterPkce())).status == "cancelled"
    assert (await owner.execute(CancelOpenRouterPkce())).status == "inactive"
    await owner.close()
    assert (await owner.execute(command())).status == "closed"
    assert client.cancelled == 1
    assert active.done()
    if not active.cancelled():
        assert (await active).status == "cancelled"


@pytest.mark.asyncio
async def test_committed_runtime_degradation_is_explicit_not_rolled_back() -> None:
    client = Client()
    handoff = Handoff(TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED)
    owner = OpenRouterPkceOwner(client_factory=lambda: client, handoff=handoff)  # type: ignore[arg-type]
    client.release.set()

    result = await owner.execute(command())

    assert result.status == "committed_degraded"
    assert result.transaction is not None
    assert result.transaction.status == TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED


@pytest.mark.asyncio
async def test_exchange_failure_is_safe_and_does_not_invoke_handoff() -> None:
    class FailingClient(Client):
        async def run_desktop_flow(self) -> OpenRouterPKCEExchangeResult:
            raise RuntimeError("provider payload must not escape")

    handoff = Handoff()
    owner = OpenRouterPkceOwner(client_factory=FailingClient, handoff=handoff)  # type: ignore[arg-type]

    result = await owner.execute(command())

    assert result.status == "failed"
    assert result.transaction is None
    assert handoff.requests == []
    assert "provider payload" not in repr(result)


@pytest.mark.asyncio
async def test_cancel_waits_for_durable_handoff_terminal_result() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class DurableHandoff(Handoff):
        async def complete_handoff(self, request):  # noqa: ANN001, ANN201
            self.requests.append(request)
            entered.set()
            await release.wait()
            return TransactionResult(
                TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED, None, None
            )

    client = Client()
    client.release.set()
    owner = OpenRouterPkceOwner(client_factory=lambda: client, handoff=DurableHandoff())  # type: ignore[arg-type]
    active = asyncio.create_task(owner.execute(command()))
    await entered.wait()

    cancelling = asyncio.create_task(owner.cancel())
    await asyncio.sleep(0)
    assert cancelling.done() is False
    assert client.cancelled == 0
    release.set()

    assert (await cancelling).status == "succeeded"
    assert (await active).status == "succeeded"
