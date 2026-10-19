from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from puripuly_heart.app.ports.broker_client import QqManagedAssertionRequest
from puripuly_heart.app.services.canonical_settings_persistence import compose_settings_owner
from puripuly_heart.app.services.managed.managed_operation import (
    MANAGED_OPERATION_RESUME_TOKEN_SECRET,
    ManagedOperationIdentity,
    write_pending_operation,
)
from puripuly_heart.app.services.qq_managed_auth import QqManagedAuthRequest, QqManagedAuthService
from puripuly_heart.app.wiring.wiring_managed_auth_factory import ManagedAuthRuntimeAdapter
from puripuly_heart.app.wiring.wiring_secrets_factory import create_secret_store
from puripuly_heart.core.openrouter.managed_openrouter_broker_client import (
    HttpManagedOpenRouterBrokerClient,
)
from tests.app.test_qq_managed_auth import (
    RecordingBrokerClient,
    RecordingManagedState,
    RecordingSecretStore,
    _success_result,
)


async def test_fresh_composed_qq_initializes_identity_before_assert(tmp_path, monkeypatch):
    monkeypatch.setenv("PURIPULY_HEART_TEST_PROFILE", "qq-auth-beta-test")
    monkeypatch.setenv("PURIPULY_HEART_TEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "synthetic-local-test-only")
    owner = compose_settings_owner(tmp_path / "settings.json")
    initial = owner.start().settings
    owner.canonical = replace(
        initial,
        intent=replace(
            initial.intent, secrets=replace(initial.intent.secrets, backend="encrypted_file")
        ),
    )
    owner.persist()
    broker = RecordingBrokerClient(_success_result())
    adapter = ManagedAuthRuntimeAdapter(
        config_path=owner.path,
        settings=owner,
        secret_store_factory=create_secret_store,
        release_service_provider=lambda: SimpleNamespace(client=broker),
        settings_repository_factory=lambda *args: None,
        runtime_presence_provider=lambda: (True, True),
        ingress_provider=lambda: False,
    )
    result = await adapter.execute_qq("100001", "a" * 64, None)
    assert result.succeeded, result.transaction_result
    assert len(broker.requests) == 1
    assert broker.requests[0].installation_id
    assert (
        owner.canonical.state.managed_connection.installation_id
        == broker.requests[0].installation_id
    )


async def test_assert_business_false_cannot_authorize_key():
    client = HttpManagedOpenRouterBrokerClient(
        "https://broker.example.test",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "ok": False,
                    "status": "issued",
                    "qq_subject_ref": "qq-test",
                    "openrouter_api_key": "synthetic-key",
                },
            )
        ),
    )
    try:
        result = await client.assert_qq_managed_identity(
            QqManagedAssertionRequest(
                qq_identity="100001",
                credential="a" * 64,
                asserted_at="2026-10-19T00:00:00.000Z",
                metadata={},
            )
        )
        assert not result.succeeded
    finally:
        await client.close()


@pytest.mark.parametrize("corruption", ["missing_token", "installation_mismatch", "orphan_token"])
async def test_pending_identity_damage_must_not_issue_new_operation(corruption):
    state = RecordingManagedState()
    store = RecordingSecretStore()
    operation = ManagedOperationIdentity("ph-mop-v1_" + "A" * 32, "qq", state.installation_id)
    write_pending_operation(state, operation)
    if corruption == "orphan_token":
        state.pending_managed_operation_id = None
        store.values[MANAGED_OPERATION_RESUME_TOKEN_SECRET] = "B" * 43
    if corruption == "installation_mismatch":
        state.pending_managed_operation_installation_id = "different-installation"
        store.values[MANAGED_OPERATION_RESUME_TOKEN_SECRET] = "B" * 43
    broker = RecordingBrokerClient(_success_result())
    service = QqManagedAuthService(broker_client=broker, secret_store=store, managed_state=state)
    result = await service.authenticate(
        QqManagedAuthRequest("100001", "a" * 64, "2026-10-19T00:00:00.000Z", max_status_polls=0)
    )
    assert broker.requests == []
    assert state.pending_managed_operation_id == (
        None if corruption == "orphan_token" else operation.operation_id
    )
    assert result.status != "settings_commit_success_runtime_applied"


@pytest.mark.parametrize("fail_at", [2, 3])
async def test_saved_delivery_survives_settings_failure_without_reissuance(
    tmp_path, monkeypatch, fail_at
):
    from puripuly_heart.core.qq_delivery_receipt import QQ_DELIVERY_RECEIPT_SECRET, decode_receipt
    from puripuly_heart.diagnostics.qq_auth_probe import ControlledBroker

    monkeypatch.setenv("PURIPULY_HEART_TEST_PROFILE", "qq-auth-beta-offline-durable")
    monkeypatch.setenv("PURIPULY_HEART_TEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "synthetic-durable-only")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    owner = compose_settings_owner(tmp_path / "settings.json")
    initial = owner.start().settings
    owner.canonical = replace(
        initial,
        intent=replace(
            initial.intent, secrets=replace(initial.intent.secrets, backend="encrypted_file")
        ),
    )
    owner.persist()

    class FaultyPersistence:
        calls = 0

        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def persist(self, *args, **kwargs):
            self.calls += 1
            if self.calls == fail_at + 1:
                raise PermissionError("synthetic settings failure")
            return self.inner.persist(*args, **kwargs)

    owner.persistence = FaultyPersistence(owner.persistence)
    with ControlledBroker() as broker:
        client = HttpManagedOpenRouterBrokerClient(broker.url)
        adapter = ManagedAuthRuntimeAdapter(
            config_path=owner.path,
            settings=owner,
            secret_store_factory=create_secret_store,
            release_service_provider=lambda: SimpleNamespace(client=client),
            settings_repository_factory=lambda *args: None,
            runtime_presence_provider=lambda: (True, False),
            ingress_provider=lambda: False,
        )
        try:
            first = await adapter.execute_qq("100001", "a" * 64, None)
            assert not first.succeeded
            stored = create_secret_store(owner.canonical.intent.secrets, config_path=owner.path)
            assert stored.get("openrouter_managed_qq_api_key") == "synthetic-local-key"
            assert decode_receipt(stored.get(QQ_DELIVERY_RECEIPT_SECRET))
            recovered = await adapter.execute_qq("100001", "a" * 64, None)
            assert recovered.succeeded, recovered.transaction_result
            assert broker.assert_count == 1
            assert decode_receipt(stored.get(QQ_DELIVERY_RECEIPT_SECRET))["acknowledged"] is True
            assert owner.canonical.state.managed_connection.pending_managed_operation_id is None
        finally:
            await client.close()


def test_encrypted_store_instances_do_not_overwrite_other_keys(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from puripuly_heart.core.storage.secrets import EncryptedFileSecretStore

    path = tmp_path / "secrets.json"
    stores = [EncryptedFileSecretStore(path, passphrase="synthetic-only") for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        list(
            pool.map(
                lambda index: stores[index].set(f"key_{index}", f"synthetic-{index}"), range(2)
            )
        )
    for store in stores:
        assert store.get("key_0") == "synthetic-0"
        assert store.get("key_1") == "synthetic-1"


async def test_duplicate_click_and_cancel_keep_the_same_pending_operation(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("PURIPULY_HEART_TEST_PROFILE", "qq-auth-beta-offline-cancel")
    monkeypatch.setenv("PURIPULY_HEART_TEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "synthetic-cancel-only")
    owner = compose_settings_owner(tmp_path / "settings.json")
    initial = owner.start().settings
    owner.canonical = replace(
        initial,
        intent=replace(
            initial.intent, secrets=replace(initial.intent.secrets, backend="encrypted_file")
        ),
    )
    owner.persist()
    entered = asyncio.Event()
    count = 0

    class HangingBroker:
        async def assert_qq_managed_identity(self, request):
            nonlocal count
            count += 1
            entered.set()
            await asyncio.Event().wait()

    adapter = ManagedAuthRuntimeAdapter(
        config_path=owner.path,
        settings=owner,
        secret_store_factory=create_secret_store,
        release_service_provider=lambda: SimpleNamespace(client=HangingBroker()),
        settings_repository_factory=lambda *args: None,
        runtime_presence_provider=lambda: (True, False),
        ingress_provider=lambda: False,
    )
    task = asyncio.create_task(adapter.execute_qq("100001", "a" * 64, None))
    await asyncio.wait_for(entered.wait(), 5)
    operation = owner.canonical.state.managed_connection.pending_managed_operation_id
    duplicate = await adapter.execute_qq("100001", "a" * 64, None)
    assert not duplicate.succeeded
    assert count == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.canonical.state.managed_connection.pending_managed_operation_id == operation
    store = create_secret_store(owner.canonical.intent.secrets, config_path=owner.path)
    assert store.get(MANAGED_OPERATION_RESUME_TOKEN_SECRET)


async def test_acknowledged_receipt_new_owner_returns_committed_and_translates(
    tmp_path, monkeypatch
):
    from puripuly_heart.diagnostics.qq_auth_probe import ControlledBroker, app_attempt

    monkeypatch.setenv("PURIPULY_HEART_TEST_PROFILE", "qq-auth-beta-offline-restart-result")
    monkeypatch.setenv("PURIPULY_HEART_TEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "synthetic-restart-only")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    owner = compose_settings_owner(tmp_path / "settings.json")
    initial = owner.start().settings
    owner.canonical = replace(
        initial,
        intent=replace(
            initial.intent, secrets=replace(initial.intent.secrets, backend="encrypted_file")
        ),
    )
    owner.persist()
    with ControlledBroker() as broker:
        first = await app_attempt("100001", "a" * 64, local_broker=broker.url)
        assert first["authentication_result"] == "committed"
        assert not broker.translation_received
        second = await app_attempt("100001", "a" * 64, local_broker=broker.url, translate=True)
        assert second["authentication_result"] == "committed"
        assert second["runtime"] == "translation_received"
        assert broker.translation_received
        assert broker.assert_count == 1
