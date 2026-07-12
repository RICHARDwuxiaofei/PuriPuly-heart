import ast
import asyncio
from dataclasses import FrozenInstanceError

import pytest

from puripuly_heart.app.adapters.ui_settings_interactions import (
    ProductionUiSettingsInteractions,
)
from puripuly_heart.app.ports.application_settings import (
    ApplicationSettingsSnapshot,
    OperationalStateSnapshot,
    SecretMetadata,
    SecretSourceStatus,
    SecretVerificationStatus,
    SettingChange,
    SettingsCommandResult,
    SettingsField,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.ports.ui_settings import (
    AudioDeviceOption,
    AudioDeviceQueryResult,
    CaptureDiagnosticReason,
    CaptureRetryResult,
    CaptureRetryStatus,
    InteractionStatus,
    LanguageSettingsSnapshot,
    ManagedAction,
    ManagedActionStatus,
    PkceStartRequest,
    RuntimeFacts,
    UiSettingsDegraded,
    UiSettingsDelta,
)
from puripuly_heart.app.services.ui_settings import (
    ApplicationUiSettingsService,
    UiSettingsApplication,
)
from puripuly_heart.app.wiring_composition import create_application_runtime_production_composition
from puripuly_heart.config.settings_vnext.facade import save_vnext_settings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


class Commands:
    def __init__(self, results=()) -> None:
        self.results = list(results)
        self.revision = "r1"
        self.settings_commands = self.settings_queries = self
        self.operational_queries = self
        self.secret_queries = self
        self.executed = []
        self.secret_value = "legacy-value"

    async def snapshot(self):
        return ApplicationSettingsSnapshot(((("ui", "locale"), "en"),), self.revision)

    async def operational_snapshot(self):
        return OperationalStateSnapshot((), "o1")

    async def secret_metadata(self, query):
        return SecretMetadata(
            query.key, True, "s1", SecretVerificationStatus.VERIFIED, SecretSourceStatus.ENVIRONMENT
        )

    async def execute(self, command):
        self.executed.append(command)
        result = self.results.pop(0)
        if isinstance(result.receipt, SettingsCommitReceipt):
            self.revision = result.receipt.revision
        return result

    async def resolve_secret_value(self, key):
        _ = key
        return self.secret_value


class Interactions:
    def __init__(self) -> None:
        self.closed = False

    async def start_pkce(self):
        await asyncio.sleep(30)

    async def close(self):
        self.closed = True


def service(commands):
    return ApplicationUiSettingsService(
        commands=commands,
        secret_keys=("openrouter_api_key",),
        runtime_facts=lambda: RuntimeFacts(True, True, "running"),
    )


@pytest.mark.asyncio
async def test_snapshot_is_explicit_immutable_redacted_and_runtime_preserving():
    snapshot = await service(Commands()).snapshot()
    assert snapshot.ui_clipboard_telemetry.locale == "en"
    assert snapshot.credentials.entries[0].source == SecretSourceStatus.ENVIRONMENT
    assert not hasattr(snapshot.credentials.entries[0], "value")
    assert snapshot.runtime.peer_active is True
    with pytest.raises(FrozenInstanceError):
        snapshot.runtime.peer_active = False


def test_nested_collections_are_deeply_immutable():
    languages = ["en"]
    options = [AudioDeviceOption("1", "wasapi", "Mic")]
    language_snapshot = LanguageSettingsSnapshot(peer_expected=languages)
    result = AudioDeviceQueryResult(InteractionStatus.APPLIED, inputs=options)
    languages.append("ja")
    options.clear()
    assert language_snapshot.peer_expected == ("en",)
    assert len(result.inputs) == 1


@pytest.mark.asyncio
async def test_exact_receipt_chain_preserves_partial_conflict_authority():
    first = SettingsCommitReceipt(AppSettingsVNext(), "r2", "translation_provider", "c1")
    commands = Commands(
        (
            SettingsCommandResult(
                "applied",
                ApplicationSettingsSnapshot((), "r2"),
                receipt=first,
                committed_revision="r2",
            ),
            SettingsCommandResult("conflict", ApplicationSettingsSnapshot((), "r3")),
        )
    )
    result = await service(commands).apply(
        UiSettingsDelta(
            "r1",
            (
                SettingChange(SettingsField.TRANSLATION_MODEL, "x"),
                SettingChange(SettingsField.UI_LOCALE, "ja"),
            ),
        )
    )
    assert isinstance(result, UiSettingsDegraded)
    assert result.outcomes[0].receipt_revision == "r2"
    assert result.outcomes[0].receipt_reason == "translation_provider"
    assert result.outcomes[0].receipt_correlation_id == "c1"
    assert result.outcomes[1].status == "conflict"


@pytest.mark.asyncio
async def test_capture_selection_uses_typed_expected_revision_command_and_receipt():
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "r2", "stt_language_audio", "capture")
    commands = Commands(
        (
            SettingsCommandResult(
                "applied",
                ApplicationSettingsSnapshot((), "r2"),
                receipt=receipt,
                committed_revision="r2",
                runtime_status="applied",
            ),
        )
    )
    application = UiSettingsApplication(service(commands), Interactions())
    result = await application.select_capture_target("device:Headphones", "r1")
    assert result.outcomes[0].receipt_revision == "r2"
    assert commands.executed[0].expected_revision == "r1"
    assert commands.executed[0].changes[0].field == SettingsField.DESKTOP_AUDIO_CAPTURE_TARGET


@pytest.mark.asyncio
async def test_capture_selection_rejects_stale_revision_without_command():
    commands = Commands()
    application = UiSettingsApplication(service(commands), Interactions())
    result = await application.select_capture_target("device:Headphones", "stale")
    assert result.actual_revision == "r1"
    assert commands.executed == []


@pytest.mark.asyncio
async def test_provider_verification_resolves_current_secret_after_rebind():
    observed = []

    class Verifier:
        async def verify_provider_secret(self, request):
            observed.append(request.secret_value)
            return type("Result", (), {"status": "verified"})()

    commands = Commands()
    adapter = ProductionUiSettingsInteractions(commands, provider_verifier=Verifier())
    await adapter.verify_provider("openrouter", "openrouter_api_key")
    commands.secret_value = "rebound-value"
    await adapter.verify_provider("openrouter", "openrouter_api_key")
    assert observed == ["legacy-value", "rebound-value"]


@pytest.mark.asyncio
async def test_verification_matches_metadata_legacy_and_environment_fallback(monkeypatch):
    from puripuly_heart.app.ports.secret_store import SecretReadResult
    from puripuly_heart.app.services.canonical_secret_commands import (
        CanonicalSecretCommandService,
    )

    class Store:
        def __init__(self, values):
            self.values = values

        async def get_secret(self, key):
            return SecretReadResult(key, self.values.get(key), None, None, None)

    class DynamicCommands(Commands):
        def bind(self, values):
            self.secret_commands = CanonicalSecretCommandService(Store(values))
            self.secret_queries = self.secret_commands

        async def resolve_secret_value(self, key):
            return await self.secret_commands.resolve_secret_value(key)

    observed = []

    class Verifier:
        async def verify_provider_secret(self, request):
            observed.append(request.secret_value)
            return type("Result", (), {"status": "verified"})()

    commands = DynamicCommands()
    commands.bind({"alibaba_api_key": "legacy"})
    adapter = ProductionUiSettingsInteractions(commands, provider_verifier=Verifier())
    metadata = await adapter.secret_metadata("alibaba_api_key_beijing")
    await adapter.verify_provider("qwen", "alibaba_api_key_beijing")
    assert metadata.present is True
    monkeypatch.setenv("ALIBABA_API_KEY_BEIJING", "environment")
    commands.bind({})
    metadata = await adapter.secret_metadata("alibaba_api_key_beijing")
    await adapter.verify_provider("qwen", "alibaba_api_key_beijing")
    assert metadata.source == SecretSourceStatus.ENVIRONMENT
    assert observed == ["legacy", "environment"]


@pytest.mark.asyncio
async def test_application_owns_active_interaction_cancellation_and_close():
    interactions = Interactions()
    application = UiSettingsApplication(service(Commands()), interactions)
    await application.start()
    task = application.run_interaction(interactions.start_pkce())
    await asyncio.sleep(0)
    await application.close()
    assert task.cancelled()
    assert interactions.closed is True


@pytest.mark.asyncio
async def test_production_wiring_constructs_starts_cancels_and_closes_without_ui(
    tmp_path, monkeypatch
):
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    composition = create_application_runtime_production_composition(path, AppSettingsVNext())
    started = []
    closed = []
    microphone = composition.ui_settings.interactions._microphone
    original_microphone_close = microphone.close

    async def close_microphone():
        closed.append("microphone")
        await original_microphone_close()

    monkeypatch.setattr(microphone, "close", close_microphone)

    async def start(*, auto_flush_osc=True):
        started.append(auto_flush_osc)

    async def shutdown():
        closed.append("runtime")

    monkeypatch.setattr(composition.runtime_host, "start", start)
    monkeypatch.setattr(composition.runtime_host, "shutdown", shutdown)
    pkce_commands = []

    async def execute_pkce(command):
        pkce_commands.append(command)
        return type("PkceResult", (), {"status": "succeeded"})()

    class ManagedResult:
        succeeded = True
        referral_id = "ref-id"
        pass_status = None

    class ManagedService:
        managed_state = type("State", (), {"referral_id": "ref-id"})()
        current_trial_remaining_percent = 42
        current_pass_status = type("Pass", (), {"invite_count": 2, "invite_limit": 5})()
        client = type(
            "Client",
            (),
            {"record_translation_success_day": lambda *_args, **_kwargs: None},
        )()

        async def refresh_managed_status(self):
            return ManagedResult()

    managed_service = ManagedService()

    async def resolve_managed():
        return managed_service

    monkeypatch.setattr(
        composition.runtime_host._runtime_composition,
        "managed_release_owner",
        type("Owner", (), {"current_service": lambda _self: managed_service})(),
        raising=False,
    )

    monkeypatch.setattr(composition.runtime_host, "execute_openrouter_pkce", execute_pkce)
    monkeypatch.setattr(
        composition.runtime_host, "resolve_managed_release_service", resolve_managed
    )

    async def retry_capture():
        return CaptureRetryResult(
            CaptureRetryStatus.FAILED,
            CaptureDiagnosticReason.TARGET_EXITED,
            "capture-r1",
        )

    monkeypatch.setattr(composition.runtime_host, "retry_peer_process_capture", retry_capture)
    await composition.start(auto_flush_osc=False)
    task = composition.ui_settings.run_interaction(asyncio.sleep(30))
    snapshot = await composition.ui_settings.settings.snapshot()
    assert snapshot.translation.fallback is not None
    assert snapshot.translation.connection_history
    assert isinstance(snapshot.translation.connection_history[0], tuple)
    assert snapshot.providers.openrouter_selection_alias is not None
    assert snapshot.providers.openrouter_broker_base_url is not None
    assert snapshot.stt.deepgram_model is not None
    assert snapshot.stt.qwen_asr_model is not None
    assert snapshot.audio.capture_target is not None
    assert snapshot.overlay.calibration is not None
    assert snapshot.overlay.desktop is not None
    assert snapshot.osc_output.vrc_mic_intercept is not None
    assert snapshot.ui_clipboard_telemetry.secrets_backend is not None
    assert snapshot.credentials.entries
    assert snapshot.ui_clipboard_telemetry.telemetry_endpoint_configured is True
    assert snapshot.managed.trial_remaining_percent == 42
    assert snapshot.managed.pass_status == "2/5"
    capture_apply = await composition.ui_settings.select_capture_target(
        "device:Headphones", snapshot.canonical_revision
    )
    assert capture_apply.outcomes[0].receipt_revision is not None
    snapshot = capture_apply.snapshot
    pkce = await composition.ui_settings.interactions.start_pkce(
        PkceStartRequest(
            "openrouter_gemma4_26b_a4b",
            "google/gemma-4-26b-a4b-it",
            snapshot.canonical_revision,
        )
    )
    assert pkce.status == InteractionStatus.APPLIED
    assert pkce_commands
    managed = await composition.ui_settings.interactions.managed_action(ManagedAction.REFRESH)
    assert managed.presentation.referral_id == "ref-id"
    capture = await composition.ui_settings.interactions.capture_targets()
    assert capture.status in {"available", "refresh_failed"}
    retry = await composition.ui_settings.interactions.retry_capture()
    assert retry.reason == CaptureDiagnosticReason.TARGET_EXITED
    await asyncio.sleep(0)
    await composition.close()
    assert started == [False]
    assert task.cancelled()
    assert closed == ["microphone", "runtime"]
    assert microphone.is_closed is True
    for source in (
        "src/puripuly_heart/app/wiring_composition.py",
        "src/puripuly_heart/app/services/ui_settings.py",
        "src/puripuly_heart/app/adapters/ui_settings_interactions.py",
    ):
        imports = [
            node.module
            for node in ast.walk(ast.parse(open(source, encoding="utf-8").read()))
            if isinstance(node, ast.ImportFrom)
        ]
        assert not any(module and module.startswith("puripuly_heart.ui") for module in imports)


@pytest.mark.asyncio
async def test_ui_application_close_retries_failed_owner():
    class FlakyInteractions(Interactions):
        attempts = 0

        async def close(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("close failed")
            self.closed = True

    interactions = FlakyInteractions()
    application = UiSettingsApplication(service(Commands()), interactions)
    await application.start()
    with pytest.raises(RuntimeError, match="close failed"):
        await application.close()
    assert application._closed is False
    await application.close()
    assert interactions.closed is True


@pytest.mark.asyncio
async def test_capture_options_retain_missing_selection_and_sort_disabled_last():
    from puripuly_heart.config.process_capture_resolution import ProcessCaptureCandidate
    from puripuly_heart.config.settings_vnext.schema import ProcessCaptureTargetIntent

    async def candidates():
        return (
            ProcessCaptureCandidate(
                "Unavailable",
                ProcessCaptureTargetIntent.vrchat(r"C:\VRChat\VRChat.exe"),
                False,
            ),
            ProcessCaptureCandidate(
                "Available",
                ProcessCaptureTargetIntent.generic_executable(r"C:\Game\Game.exe"),
                True,
            ),
        )

    adapter = ProductionUiSettingsInteractions(Commands(), capture_candidates=candidates)
    snapshot = await adapter.capture_targets("process:generic_executable:c:\\missing.exe")
    assert snapshot.options[0].target_id == "process:generic_executable:c:\\missing.exe"
    assert snapshot.options[-1].available is False


def test_structured_snapshot_pairs_are_deeply_immutable():
    from puripuly_heart.app.ports.ui_settings import (
        PromptSettingsSnapshot,
        TranslationSettingsSnapshot,
    )

    history = [["gemma4", "managed"]]
    prompts = [["openrouter", "prompt"]]
    translation = TranslationSettingsSnapshot(connection_history=history)
    prompt = PromptSettingsSnapshot(provider_prompts=prompts)
    history[0][1] = "changed"
    prompts[0][1] = "changed"
    assert translation.connection_history == (("gemma4", "managed"),)
    assert prompt.provider_prompts == (("openrouter", "prompt"),)


@pytest.mark.asyncio
async def test_telemetry_endpoint_truth_comes_from_actual_client_capability():
    class TelemetryOwner:
        async def load(self):
            return type("Telemetry", (), {"consent": "allow"})()

    class Host:
        service = type("Managed", (), {"client": object()})()

        @property
        def managed_release_service(self):
            return self.service

    host = Host()
    adapter = ProductionUiSettingsInteractions(
        Commands(), runtime_host=host, telemetry_owner=TelemetryOwner()
    )
    assert (await adapter.telemetry_presentation()).endpoint_configured is False
    host.service = type(
        "Managed",
        (),
        {
            "client": type(
                "Client",
                (),
                {"record_translation_success_day": lambda *_args: None},
            )()
        },
    )()
    assert (await adapter.telemetry_presentation()).endpoint_configured is True


@pytest.mark.asyncio
async def test_managed_presentation_is_pure_and_refresh_updates_cached_owner_state():
    class Service:
        managed_state = type("State", (), {"referral_id": "before"})()
        current_trial_remaining_percent = 10
        current_pass_status = type("Pass", (), {"invite_count": 1, "invite_limit": 4})()
        calls = 0

        async def refresh_presentation_state(self, verifier):
            _ = verifier
            self.calls += 1
            self.current_trial_remaining_percent = 75
            self.current_pass_status = type("Pass", (), {"invite_count": 3, "invite_limit": 4})()
            self.managed_state.referral_id = "after"

    service = Service()

    class Host:
        resolves = 0

        @property
        def managed_release_service(self):
            return service

        async def resolve_managed_release_service(self):
            self.resolves += 1
            return service

    host = Host()
    adapter = ProductionUiSettingsInteractions(Commands(), runtime_host=host)
    before = await adapter.managed_presentation()
    assert service.calls == 0
    assert host.resolves == 0
    assert before.trial_remaining_percent == 10
    deferred = await adapter.managed_action(ManagedAction.CONNECT)
    assert deferred.status == ManagedActionStatus.DEFERRED
    assert host.resolves == 0
    result = await adapter.managed_action(ManagedAction.REFRESH)
    assert result.status == ManagedActionStatus.APPLIED
    assert service.calls == 1
    assert host.resolves == 1
    assert result.presentation.trial_remaining_percent == 75
    assert result.presentation.pass_status == "3/4"
    assert result.presentation.referral_id == "after"


@pytest.mark.asyncio
async def test_managed_refresh_maps_failure_and_cancellation_without_snapshot_io():
    class Service:
        managed_state = type("State", (), {"referral_id": "safe"})()
        current_trial_remaining_percent = None
        current_pass_status = None
        failure = RuntimeError("failed")

        async def refresh_presentation_state(self, verifier):
            _ = verifier
            raise self.failure

    service = Service()

    class Host:
        resolves = 0

        @property
        def managed_release_service(self):
            return service

        async def resolve_managed_release_service(self):
            self.resolves += 1
            return service

    host = Host()
    adapter = ProductionUiSettingsInteractions(Commands(), runtime_host=host)
    failed = await adapter.managed_action(ManagedAction.REFRESH)
    assert failed.status == ManagedActionStatus.FAILED
    assert host.resolves == 1
    service.failure = asyncio.CancelledError()
    cancelled = await adapter.managed_action(ManagedAction.REFRESH)
    assert cancelled.status == ManagedActionStatus.CANCELLED
    assert host.resolves == 2
    assert (await adapter.managed_presentation()).referral_id == "safe"
    assert host.resolves == 2
