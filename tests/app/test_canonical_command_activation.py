from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.ports.application_settings import (
    ClearSecretCommand,
    GithubStarClickedCommand,
    OperationalCommandResult,
    OperationalStateSnapshot,
    SecretMetadataQuery,
    SecretSourceStatus,
    SetSecretCommand,
    SettingChange,
    SettingsField,
    TranslationProviderSettingsCommand,
    UiPromptClipboardSettingsCommand,
)
from puripuly_heart.app.ports.post_commit_runtime import (
    PostCommitRuntimeExecutionResult,
    RuntimeOperationalSnapshot,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.application_runtime_host import (
    ApplicationRuntimeHost,
    ApplicationRuntimeParts,
)
from puripuly_heart.app.services.canonical_command_composition import (
    PRODUCTION_SETTINGS_SURFACES,
    create_canonical_command_composition,
    dispatch_result_as_transaction,
    receipt_runtime_trace,
)
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.app.services.canonical_secret_commands import (
    _SECRET_KEY_FALLBACKS,
    CanonicalSecretCommandService,
    SyncSecretStorePortAdapter,
)
from puripuly_heart.app.wiring import create_secret_store
from puripuly_heart.app.wiring_composition import (
    create_application_runtime_production_composition,
)
from puripuly_heart.config.settings import (
    SecretsBackend,
    SecretsSettings,
)
from puripuly_heart.config.settings_vnext.facade import save_vnext_settings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.messages import (
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
    TransactionResult,
)
from puripuly_heart.core.runtime.peer_channel import PeerChannelRuntimeState, PeerPolicySnapshot
from puripuly_heart.core.runtime.self_audio import SelfChannelSnapshot, SelfChannelState
from puripuly_heart.core.storage.secrets import InMemorySecretStore


class RecordingHost:
    def __init__(self, *, fail_surfaces: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, str | None, str]] = []
        self.fail_surfaces = fail_surfaces
        self.operational_calls = 0
        self.skipped = ("dashboard_retry_facts",)
        self.canonical_commands = None
        self.bound_secrets = None

    async def rebind_secret_store(self, secrets: object, receipt: SettingsCommitReceipt) -> None:
        _ = receipt
        self.bound_secrets = secrets

    def runtime_operational_snapshot(self) -> RuntimeOperationalSnapshot:
        self.operational_calls += 1
        return RuntimeOperationalSnapshot(
            translation_enabled=True,
            self_stt_enabled=True,
            self_stt_running=False,
            self_stt_staged=True,
            peer_stt_enabled=False,
            peer_stt_running=False,
            peer_stt_staged=True,
            llm_available=True,
            llm_retry_pending=False,
            self_stt_available=True,
            self_stt_retry_pending=False,
            peer_stt_available=True,
            peer_stt_retry_pending=False,
        )

    async def apply_committed_runtime(self, *, before, after, surface, operational):  # noqa: ANN001
        before_revision = None if before is None else before.revision
        self.calls.append((surface, before_revision, after.revision))
        assert operational.translation_enabled is True
        assert before is not None
        assert before.revision != after.revision
        if surface in self.fail_surfaces:
            return PostCommitRuntimeExecutionResult(
                TransactionResult(
                    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
                    None,
                    None,
                ),
                completed=(),
                failed="provider_activation",
                skipped=self.skipped,
                reconciliation_required=True,
            )
        return PostCommitRuntimeExecutionResult(
            TransactionResult(
                TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
                None,
                None,
            ),
            completed=("provider_activation",),
            failed=None,
            skipped=self.skipped,
            reconciliation_required=False,
        )


def _seed_settings(path: Path) -> None:
    assert save_vnext_settings(path, AppSettingsVNext()).ok


@pytest.mark.asyncio
async def test_secret_fallback_after_clear_and_compatible_lookup(tmp_path, monkeypatch) -> None:
    store = InMemorySecretStore()
    service = CanonicalSecretCommandService(SyncSecretStorePortAdapter(store), store_kind=store)
    await service.set_secret(SetSecretCommand("openrouter_api_key", "super-secret"))
    store.set("alibaba_api_key", "legacy-secret")
    meta = await service.secret_metadata(SecretMetadataQuery("alibaba_api_key_beijing"))
    assert meta.present is True
    assert meta.source == SecretSourceStatus.KEYRING
    assert "legacy-secret" not in repr(meta)

    monkeypatch.setenv("GOOGLE_API_KEY", "env-secret")
    await service.clear_secret(ClearSecretCommand("google_api_key"))
    after_clear = await service.secret_metadata(SecretMetadataQuery("google_api_key"))
    assert after_clear.present is True
    assert after_clear.source == SecretSourceStatus.ENVIRONMENT
    assert "env-secret" not in repr(after_clear)

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    cleared = await service.clear_secret(ClearSecretCommand("openrouter_api_key"))
    assert cleared.present is False


def test_encrypted_file_secret_store_requires_passphrase(tmp_path) -> None:
    with pytest.raises(ValueError, match="PURIPULY_HEART_SECRETS_PASSPHRASE"):
        create_secret_store(
            SecretsSettings(
                backend=SecretsBackend.ENCRYPTED_FILE,
                encrypted_file_path="secrets.json",
            ),
            config_path=tmp_path / "settings.json",
        )


@pytest.mark.asyncio
def _bind(composition, host: RecordingHost):
    host.canonical_commands = composition
    return composition


async def test_mixed_surface_dispatch_returns_composite_aggregate_result(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    before = AppSettingsVNext()
    after = replace(
        before,
        intent=replace(
            before.intent,
            translation=replace(before.intent.translation, model="deepseek_v4_flash"),
            languages=replace(before.intent.languages, source_language="en"),
            overlay=replace(before.intent.overlay, show_translation=False),
            ui=replace(before.intent.ui, locale="ja"),
        ),
        state=replace(
            before.state,
            github_star_prompt=replace(before.state.github_star_prompt, clicked=True),
            peer_translation=replace(before.state.peer_translation, eula_accepted=True),
        ),
    )
    dispatch = await composition.execute_production_settings_delta(before=before, after=after)
    assert dispatch.status == "applied"
    surfaces = {surface for surface, _ in dispatch.surface_results}
    assert surfaces == {
        "translation_provider",
        "stt_language_audio",
        "overlay_osc_output",
        "ui_prompt_clipboard_state",
    }
    for surface, result in dispatch.surface_results:
        assert isinstance(result.receipt, SettingsCommitReceipt)
        assert result.runtime_skipped == ("dashboard_retry_facts",)
        assert result.receipt.revision == result.snapshot.revision
        assert surface in PRODUCTION_SETTINGS_SURFACES
    assert len(dispatch.operational_results) == 2
    for op in dispatch.operational_results:
        assert op.runtime_outcome == "no_runtime_change"
        assert isinstance(op.receipt, SettingsCommitReceipt)
    transaction = dispatch_result_as_transaction(dispatch)
    assert transaction.status == TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED
    assert transaction.diagnostics is not None
    assert transaction.diagnostics.fields["aggregate_status"] == "applied"
    assert "translation_provider:applied" in str(transaction.diagnostics.fields["surface_results"])
    assert host.calls


@pytest.mark.asyncio
async def test_no_change_returns_current_authoritative_receipt_without_commit(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    before = AppSettingsVNext()
    dispatch = await composition.execute_production_settings_delta(before=before, after=before)
    assert dispatch.status == "no_change"
    assert dispatch.surface_results[0][1].no_op is True
    assert isinstance(dispatch.final_receipt, SettingsCommitReceipt)
    assert dispatch.final_receipt.reason == "current_state"
    assert host.calls == []


@pytest.mark.asyncio
async def test_forced_runtime_failure_preserves_receipt_and_skipped(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost(fail_surfaces=frozenset({"ui_prompt_clipboard_state"}))
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    before = await composition.settings_commands.snapshot()
    result = await composition.settings_commands.execute(
        UiPromptClipboardSettingsCommand(
            (SettingChange(SettingsField.UI_LOCALE, "ko"),),
            before.revision,
            "c-fail",
        )
    )
    assert result.status == "degraded"
    assert isinstance(result.receipt, SettingsCommitReceipt)
    assert result.runtime_failed == "provider_activation"
    assert result.runtime_skipped == ("dashboard_retry_facts",)
    assert result.reconciliation_required is True


@pytest.mark.asyncio
async def test_operational_no_change_and_commit_have_typed_receipts(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    revision = (await composition.operational_commands.operational_snapshot()).revision
    first = await composition.operational_commands.execute_operational(
        GithubStarClickedCommand(True, revision)
    )
    assert first.status == "committed"
    assert first.runtime_outcome == "no_runtime_change"
    assert isinstance(first.receipt, SettingsCommitReceipt)
    second = await composition.operational_commands.execute_operational(
        GithubStarClickedCommand(True, first.snapshot.revision)
    )
    assert second.status == "no_change"
    assert second.no_op is True
    assert second.runtime_outcome == "no_runtime_change"
    assert isinstance(second.receipt, SettingsCommitReceipt)
    assert second.receipt.revision == first.snapshot.revision


@pytest.mark.asyncio
async def test_host_operational_snapshot_uses_self_and_peer_state_contracts() -> None:
    class Hub:
        def provider_state_snapshot(self):  # noqa: ANN201
            return type(
                "S", (), {"llm": type("L", (), {"provider": object(), "generation": 1})()}
            )()

        def lease_stt_provider(self, slot):  # noqa: ANN001, ANN201
            if slot == "self_stt":
                return type("Lease", (), {"current": object()})()
            return type("Lease", (), {"current": object()})()

    class SelfOwner:
        def snapshot(self):  # noqa: ANN201
            return SelfChannelSnapshot(
                desired_enabled=False,
                state=SelfChannelState.STOPPED,
                provider_available=True,
                generation=1,
                runtime_signature=None,
                intent_generation=2,
                intent_enabled=False,
            )

    class PeerOwner:
        state = PeerChannelRuntimeState.STOPPED

        def policy_snapshot(self):  # noqa: ANN201
            return PeerPolicySnapshot(1, False, 3, False)

    host = ApplicationRuntimeHost(
        parts=ApplicationRuntimeParts(
            sender=type("S", (), {"close": lambda self: None})(),
            osc=object(),
            hub=Hub(),
            peer_runtime=PeerOwner(),
            self_stt=SelfOwner(),
        ),
        runtime_composition=type(
            "C",
            (),
            {
                "resolved_adapter": object(),
                "surface_transactions": object(),
                "close": lambda self: None,
            },
        )(),
        committed_settings=type("R", (), {"load_receipt": lambda self: None})(),
        resolver=CanonicalRuntimeConfigResolver(),
    )
    snap = host.runtime_operational_snapshot()
    assert snap.self_stt_enabled is False
    assert snap.self_stt_running is False
    assert snap.self_stt_staged is True
    assert snap.peer_stt_enabled is False
    assert snap.peer_stt_running is False
    assert snap.peer_stt_staged is True
    assert snap.self_stt_available is True
    assert snap.peer_stt_available is True


@pytest.mark.asyncio
async def test_secrets_backend_rebind_is_runtime_operation(tmp_path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    before = AppSettingsVNext()
    after = replace(
        before,
        intent=replace(
            before.intent,
            secrets=replace(
                before.intent.secrets,
                backend="encrypted_file",
                encrypted_file_path="secrets.json",
            ),
        ),
    )
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "unit-test-passphrase")
    dispatch = await composition.execute_production_settings_delta(before=before, after=after)
    assert dispatch.status == "applied"
    assert dispatch.secrets_rebound is True
    assert host.bound_secrets is not None
    ui_result = next(
        result
        for surface, result in dispatch.surface_results
        if surface == "ui_prompt_clipboard_state"
    )
    assert "secrets_backend_rebind" in ui_result.runtime_completed
    await composition.secret_commands.set_secret(SetSecretCommand("openrouter_api_key", "x"))
    meta = await composition.secret_commands.secret_metadata(
        SecretMetadataQuery("openrouter_api_key")
    )
    assert meta.present is True
    assert meta.source == SecretSourceStatus.ENCRYPTED_FILE


@pytest.mark.asyncio
async def test_secrets_backend_passphrase_failure_is_declared_reconciliation(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    before = AppSettingsVNext()
    after = replace(
        before,
        intent=replace(
            before.intent,
            secrets=replace(
                before.intent.secrets,
                backend="encrypted_file",
                encrypted_file_path="secrets.json",
            ),
        ),
    )
    monkeypatch.delenv("PURIPULY_HEART_SECRETS_PASSPHRASE", raising=False)
    dispatch = await composition.execute_production_settings_delta(before=before, after=after)
    assert dispatch.status == "degraded"
    assert dispatch.reconciliation_required is True
    ui_result = next(
        result
        for surface, result in dispatch.surface_results
        if surface == "ui_prompt_clipboard_state"
    )
    assert ui_result.status == "degraded"
    assert ui_result.runtime_failed == "secrets_backend_rebind"
    assert isinstance(ui_result.receipt, SettingsCommitReceipt)
    assert dispatch.final_receipt.revision == ui_result.receipt.revision


@pytest.mark.asyncio
async def test_early_surface_degraded_keeps_later_success_as_degraded(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost(fail_surfaces=frozenset({"translation_provider"}))
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    before = AppSettingsVNext()
    after = replace(
        before,
        intent=replace(
            before.intent,
            translation=replace(before.intent.translation, model="deepseek_v4_flash"),
            languages=replace(before.intent.languages, source_language="en"),
            ui=replace(before.intent.ui, locale="ja"),
        ),
    )
    dispatch = await composition.execute_production_settings_delta(before=before, after=after)
    assert dispatch.status == "degraded"
    statuses = {surface: result.status for surface, result in dispatch.surface_results}
    assert statuses["translation_provider"] == "degraded"
    assert statuses["stt_language_audio"] == "applied"
    assert statuses["ui_prompt_clipboard_state"] == "applied"
    assert all(
        isinstance(result.receipt, SettingsCommitReceipt) for _, result in dispatch.surface_results
    )
    transaction = dispatch_result_as_transaction(dispatch)
    assert transaction.status == TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED
    assert transaction.diagnostics.fields["aggregate_status"] == "degraded"


@pytest.mark.asyncio
async def test_later_operational_failure_is_not_ignored(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    before = AppSettingsVNext()
    after = replace(
        before,
        intent=replace(
            before.intent,
            ui=replace(before.intent.ui, locale="ja"),
        ),
        state=replace(
            before.state,
            github_star_prompt=replace(before.state.github_star_prompt, clicked=True),
        ),
    )
    original = composition.operational_commands.execute_operational

    async def fail_operational(command):  # noqa: ANN001
        if isinstance(command, GithubStarClickedCommand):
            return OperationalCommandResult(
                "read_failed",
                OperationalStateSnapshot((), "unavailable"),
                runtime_outcome="no_runtime_change",
            )
        return await original(command)

    composition.operational_commands.execute_operational = fail_operational  # type: ignore[method-assign]
    dispatch = await composition.execute_production_settings_delta(before=before, after=after)
    assert dispatch.partial_commit is True
    assert dispatch.reconciliation_required is True
    assert dispatch.status == "partial_commit_degraded"
    assert dispatch.surface_results
    assert isinstance(dispatch.surface_results[0][1].receipt, SettingsCommitReceipt)
    assert dispatch.final_receipt.revision == (await composition.current_receipt()).revision
    assert dispatch.final_receipt.envelope.intent.ui.locale == "ja"


def test_local_llm_api_key_env_fallback_is_not_claimed() -> None:
    assert "local_llm_api_key" not in _SECRET_KEY_FALLBACKS


@pytest.mark.asyncio
async def test_first_surface_revision_conflict_has_no_partial_commit(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    before = AppSettingsVNext()
    after = replace(
        before,
        intent=replace(
            before.intent,
            translation=replace(before.intent.translation, model="deepseek_v4_flash"),
        ),
    )
    dispatch = await composition.execute_production_settings_delta(
        before=before,
        after=after,
        expected_revision="stale-revision",
    )
    assert dispatch.status == "conflict"
    assert dispatch.partial_commit is False
    assert dispatch.reconciliation_required is False
    assert dispatch.surface_results[0][1].receipt is None
    assert dispatch.final_receipt.envelope == before
    assert dispatch_result_as_transaction(dispatch).status not in {
        TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
        TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
    }


@pytest.mark.asyncio
async def test_failed_secret_rebind_keeps_composition_secret_authority(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    old_store = InMemorySecretStore()
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "unit-test-passphrase")

    class FailingHost(RecordingHost):
        async def rebind_secret_store(
            self, secrets: object, receipt: SettingsCommitReceipt
        ) -> None:
            _ = (secrets, receipt)
            raise asyncio.CancelledError

    host = FailingHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=old_store,
        ),
        host,
    )
    old_service = composition.secret_commands
    before = AppSettingsVNext()
    after = replace(
        before,
        intent=replace(
            before.intent,
            secrets=replace(
                before.intent.secrets,
                backend="encrypted_file",
                encrypted_file_path="secrets.json",
            ),
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await composition.execute_production_settings_delta(before=before, after=after)

    assert composition._secrets is old_store
    assert composition.secret_commands is old_service


@pytest.mark.asyncio
async def test_committed_secret_rebind_cancellation_publishes_then_propagates(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    old_store = InMemorySecretStore()
    new_store = InMemorySecretStore()

    class CommittedCancellationHost(RecordingHost):
        committed = False

        async def rebind_secret_store(
            self, secrets: object, receipt: SettingsCommitReceipt
        ) -> None:
            _ = receipt
            assert secrets is new_store
            self.committed = True
            raise asyncio.CancelledError

        def secret_store_is_authoritative(self, secrets: object) -> bool:
            return self.committed and secrets is new_store

    host = CommittedCancellationHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=old_store,
        ),
        host,
    )
    import puripuly_heart.app.wiring as wiring

    monkeypatch.setattr(wiring, "create_secret_store", lambda settings, config_path: new_store)

    receipt = SettingsCommitReceipt(AppSettingsVNext(), "r2", "rebind", None)
    with pytest.raises(asyncio.CancelledError):
        await composition.try_rebind_secrets_from_intent(receipt)

    assert composition._secrets is new_store
    assert composition.secret_commands is not None


@pytest.mark.asyncio
async def test_production_commands_include_skipped_metadata(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    host = RecordingHost()
    composition = _bind(
        create_canonical_command_composition(
            state_path=path,
            runtime_host=host,
            secrets=InMemorySecretStore(),
        ),
        host,
    )
    before = await composition.settings_commands.snapshot()
    result = await composition.settings_commands.execute(
        TranslationProviderSettingsCommand(
            (SettingChange(SettingsField.TRANSLATION_MODEL, "deepseek_v4_flash"),),
            before.revision,
            "c-1",
        )
    )
    assert result.status == "applied"
    assert result.runtime_completed == ("provider_activation",)
    assert result.runtime_skipped == ("dashboard_retry_facts",)
    trace = receipt_runtime_trace(result, surface="translation_provider")
    assert trace["runtime_skipped"] == ("dashboard_retry_facts",)


@pytest.mark.asyncio
async def test_production_runtime_host_composition_binds_all_surfaces(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _seed_settings(path)
    composition = create_application_runtime_production_composition(path, AppSettingsVNext())
    host = composition.runtime_host
    assert composition.canonical_commands is host.canonical_commands
    assert host._runtime_composition.surface_transactions.migrated_surfaces == (
        PRODUCTION_SETTINGS_SURFACES
    )
    await host.shutdown()


def test_production_canonical_composition_has_no_ui_imports() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "puripuly_heart" / "app"
    paths = (
        root / "services" / "canonical_command_composition.py",
        root / "services" / "canonical_secret_commands.py",
        root / "services" / "canonical_application_settings.py",
        root / "wiring_composition.py",
        root / "adapters" / "application_runtime_production.py",
    )
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert all(
            not module.startswith("puripuly_heart.ui") and module != "flet" for module in imports
        ), path
