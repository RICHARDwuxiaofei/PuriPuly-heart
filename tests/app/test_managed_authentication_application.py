from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from puripuly_heart.app.adapters.managed_authentication_production import (
    ProductionManagedAuthenticationBrowser,
    create_production_managed_authentication_application,
)
from puripuly_heart.app.ports.broker_client import (
    ManagedKeyDeliveryAckResult,
    QqManagedAssertionResult,
    QqManagedEntitlementSnapshot,
)
from puripuly_heart.app.ports.managed_authentication_application import (
    EphemeralSecretLease,
    ManagedAuthenticationPrompt,
    ManagedAuthenticationStatus,
    StartDiscordManagedAuthentication,
    StartQqManagedAuthentication,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.canonical_secret_commands import (
    CanonicalSecretCommandService,
    SyncSecretStorePortAdapter,
)
from puripuly_heart.app.services.managed_authentication_application import (
    ManagedAuthenticationApplication,
    managed_authentication_presentation,
)
from puripuly_heart.app.services.managed_canonical_transaction import (
    ack_delivered_secret_key,
    encode_ack_delivery_confirmation,
)
from puripuly_heart.app.wiring_composition import (
    create_application_runtime_production_composition,
)
from puripuly_heart.app.wiring_managed_auth_factory import (
    ManagedIdentityStateAdapter,
    build_openrouter_release_runtime_config,
)
from puripuly_heart.config.settings import (
    AppSettings,
    OpenRouterCredentialSource,
    TranslationConnection,
)
from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.facade import save_vnext_settings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterDiscordStartSuccess,
    ManagedOpenRouterFingerprintSalt,
    ManagedOpenRouterIssueSuccess,
    ManagedOpenRouterReleaseService,
)
from puripuly_heart.core.messages import RuntimeApplyResult
from puripuly_heart.core.runtime.oauth import OAuthRuntime


def _qq_owner(*, presentation=None) -> ManagedAuthenticationApplication:  # noqa: ANN001
    async def default_presentation():  # noqa: ANN202
        return managed_authentication_presentation(
            action="prompt",
            prompt="qq",
            connection_state="disconnected",
            browser_reopen_available=False,
            referral_bonus_applied=False,
        )

    async def qq(_identity, _credential):  # noqa: ANN001, ANN202
        return "applied", None

    class Browser:
        async def reopen(self) -> bool:
            return False

        async def cancel(self) -> None:
            return None

    return ManagedAuthenticationApplication(
        presentation=presentation or default_presentation,
        start_discord=lambda _referral: qq("", ""),
        start_qq=qq,
        browser=Browser(),
        close_authentication=Browser().cancel,
        oauth_runtime=OAuthRuntime(),
    )


@pytest.mark.asyncio
async def test_managed_authentication_owner_returns_credential_free_typed_results() -> None:
    events: list[object] = []
    callback = [False]

    async def presentation():  # noqa: ANN202
        return managed_authentication_presentation(
            action="prompt",
            prompt="qq",
            connection_state="disconnected",
            browser_reopen_available=True,
            referral_bonus_applied=False,
            generation=3,
            callback_received=callback[0],
        )

    async def discord(referral_id):  # noqa: ANN001, ANN202
        events.append(("discord", referral_id))
        return "applied", None

    async def qq(identity, credential):  # noqa: ANN001, ANN202
        events.append(("qq", identity, len(credential)))
        return "rejected", "qq_auth.error.retry"

    class Browser:
        async def reopen(self) -> bool:
            events.append("reopen")
            return True

        async def cancel(self) -> None:
            events.append("cancel")

    async def close() -> None:
        events.append("close")

    owner = ManagedAuthenticationApplication(
        presentation=presentation,
        start_discord=discord,
        start_qq=qq,
        browser=Browser(),
        close_authentication=close,
        oauth_runtime=OAuthRuntime(),
    )
    presentations = []
    unsubscribe = owner.subscribe_presentation(presentations.append)

    initial = await owner.presentation()
    discord_result = await owner.start_discord(StartDiscordManagedAuthentication("ref"))
    lease = EphemeralSecretLease.from_text("secret")
    qq_result = await owner.start_qq(StartQqManagedAuthentication("42", lease))
    callback[0] = True
    owner.set_callback_received()
    callback_presentation = await owner.presentation()
    reopen_result = await owner.reopen_discord_browser()
    cancel_result = await owner.cancel()
    await owner.close()
    unsubscribe()

    assert initial.prompt == ManagedAuthenticationPrompt.QQ
    assert discord_result.status == ManagedAuthenticationStatus.APPLIED
    assert qq_result.status == ManagedAuthenticationStatus.REJECTED
    assert reopen_result.status == ManagedAuthenticationStatus.APPLIED
    assert cancel_result.status == ManagedAuthenticationStatus.CANCELLED
    assert callback_presentation.callback_received is True
    assert "secret" not in repr(qq_result)
    assert "secret" not in repr(vars(owner))
    assert lease.consumed is True
    assert "secret" not in repr(lease)
    assert any(item.action == "in_progress" for item in presentations)
    assert any(item.callback_received for item in presentations)
    assert presentations[-1].action != "in_progress"
    assert events == [
        ("discord", "ref"),
        ("qq", "42", 6),
        "reopen",
        "cancel",
        "cancel",
        "close",
    ]


def test_ephemeral_secret_lease_clear_and_consume_are_terminal() -> None:
    cleared = EphemeralSecretLease.from_text("secret")
    cleared.clear()
    assert cleared.consumed is True
    with pytest.raises(RuntimeError, match="already consumed"):
        cleared.consume()

    consumed = EphemeralSecretLease.from_text("secret")
    assert consumed.consume() == "secret"
    with pytest.raises(RuntimeError, match="already consumed"):
        consumed.consume()


@pytest.mark.asyncio
async def test_qq_lease_clears_when_pending_listener_fails() -> None:
    owner = _qq_owner()
    owner.subscribe_presentation(lambda _presentation: (_ for _ in ()).throw(RuntimeError("boom")))
    lease = EphemeralSecretLease.from_text("secret")

    with pytest.raises(RuntimeError, match="boom"):
        await owner.start_qq(StartQqManagedAuthentication("42", lease))

    assert lease.consumed is True
    assert (await owner.presentation()).action != "in_progress"


@pytest.mark.asyncio
async def test_qq_lease_clears_when_cancelled_before_consume() -> None:
    entered = asyncio.Event()

    async def blocked_presentation():  # noqa: ANN202
        entered.set()
        await asyncio.Event().wait()

    owner = _qq_owner(presentation=blocked_presentation)
    lease = EphemeralSecretLease.from_text("secret")
    task = asyncio.create_task(owner.start_qq(StartQqManagedAuthentication("42", lease)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert lease.consumed is True
    assert owner._in_progress is False


@pytest.mark.asyncio
async def test_production_managed_browser_owns_reopen_and_named_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []

    class Runtime:
        async def cancel_auth_task(self, name: str) -> None:
            events.append(("cancel", name))

    monkeypatch.setattr(
        "puripuly_heart.app.adapters.managed_authentication_production.webbrowser.open",
        lambda url: events.append(("open", url)) or True,
    )
    browser = ProductionManagedAuthenticationBrowser(Runtime())
    browser.set_authorization_url("https://discord.test/auth")

    assert browser.available is True
    assert await browser.reopen() is True
    await browser.cancel()

    assert browser.available is False
    assert events == [
        ("open", "https://discord.test/auth"),
        ("cancel", "managed-discord"),
        ("cancel", "managed-qq"),
    ]


@pytest.mark.asyncio
async def test_production_managed_authentication_projects_callback_reopen_and_cancel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    callbacks = []
    opened_urls: list[str] = []

    class RuntimeHost:
        def subscribe_managed_discord_callback(self, callback):  # noqa: ANN001, ANN202
            callbacks.append(callback)

        async def resolve_managed_release_service(self):  # noqa: ANN201
            return release

    class UiSettings:
        async def snapshot(self):  # noqa: ANN202
            return SimpleNamespace(
                translation=SimpleNamespace(connection="managed"),
                credentials=SimpleNamespace(
                    entries=(SimpleNamespace(key="openrouter_managed_api_key", present=False),)
                ),
                managed=SimpleNamespace(
                    trial_remaining_percent=75,
                    referral_id="referral",
                    pass_status="active",
                ),
            )

    monkeypatch.setattr(
        "puripuly_heart.app.adapters.managed_authentication_production.webbrowser.open",
        lambda url: opened_urls.append(url) or True,
    )

    class Listener:
        redirect_uri = "http://127.0.0.1:62187/discord/callback"

        def close(self) -> None:
            return None

    class Client:
        async def start_discord_oauth(self, **kwargs):  # noqa: ANN003, ANN201
            return ManagedOpenRouterDiscordStartSuccess(
                "https://discord.test/auth",
                kwargs["redirect_uri"],
                "2099-01-01T00:00:00Z",
                "nonce",
                ManagedOpenRouterFingerprintSalt(1, "salt"),
                1,
            )

        async def issue_discord_managed_key(self, _request):  # noqa: ANN001, ANN201
            callbacks[0](object())
            return ManagedOpenRouterIssueSuccess(
                "managed-secret",
                managed_credential_ref="credential",
                referral_bonus_applied=True,
            )

    secrets = _SyncSecrets()
    persistence = _ManagedPersistence()
    secret_port = SyncSecretStorePortAdapter(secrets)
    canonical = SimpleNamespace(
        secret_commands=CanonicalSecretCommandService(secret_port),
        _secret_port=secret_port,
        runtime_apply=_AppliedRuntime(),
    )
    legacy = AppSettings()
    legacy.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    release = ManagedOpenRouterReleaseService(
        openrouter_config=build_openrouter_release_runtime_config(legacy),
        managed_state=ManagedIdentityStateAdapter(legacy, lambda _settings: None),
        secrets=secrets,
        client=Client(),
        app_version="test",
        raw_hardware_fingerprint_provider=lambda: "hardware",
        discord_oauth_listener_factory=Listener,
        discord_oauth_callback_runner=lambda *_args: asyncio.sleep(0, result=("code", "state")),
    )
    owner = create_production_managed_authentication_application(
        runtime_host=RuntimeHost(),
        ui_settings=UiSettings(),
        secrets=secrets,
        oauth_runtime=OAuthRuntime(),
        canonical_commands=canonical,
        persistence=persistence,
        state_path=tmp_path / "settings.json",
    )

    result = await owner.start_discord(StartDiscordManagedAuthentication("referral"))
    presentation = await owner.presentation()
    reopen_result = await owner.reopen_discord_browser()
    cancel_result = await owner.cancel()
    cancelled_presentation = await owner.presentation()

    assert result.status == ManagedAuthenticationStatus.APPLIED
    assert presentation.callback_received is True
    assert presentation.browser_reopen_available is True
    assert presentation.referral_bonus_applied is True
    assert reopen_result.status == ManagedAuthenticationStatus.APPLIED
    assert opened_urls == ["https://discord.test/auth"]
    assert cancel_result.status == ManagedAuthenticationStatus.CANCELLED
    assert cancelled_presentation.browser_reopen_available is False
    assert owner.managed_transactions.authentication_owner is owner


@pytest.mark.asyncio
async def test_production_application_recovers_persisted_ack_without_provider_claim(
    tmp_path: Path,
) -> None:
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        state=replace(
            settings.state,
            managed_connection=replace(
                settings.state.managed_connection,
                pending_delivery_ack_source="discord",
                pending_delivery_ack_delivery_id="delivery",
                pending_delivery_ack_managed_credential_ref="credential",
            ),
        ),
    )
    persistence = _ManagedPersistence(settings)
    secrets = _SyncSecrets()
    secrets.set("openrouter_managed_delivery_ack_token", "ack-token")

    class State:
        pending_delivery_ack_source = "discord"
        pending_delivery_ack_delivery_id = "delivery"
        pending_delivery_ack_managed_credential_ref = "credential"
        pending_delivery_ack_expires_at = None

    class Client:
        ack_calls = 0

        calls = 0

        async def acknowledge_managed_key_delivery(self, request):  # noqa: ANN001, ANN201
            self.calls += 1
            assert request.delivery_ack_token == "ack-token"
            return ManagedKeyDeliveryAckResult(True, "acknowledged")

    release = SimpleNamespace(managed_state=State(), client=Client())

    class RuntimeHost:
        def subscribe_managed_discord_callback(self, _callback):  # noqa: ANN001
            return None

        async def resolve_managed_release_service(self):  # noqa: ANN201
            return release

    class UiSettings:
        async def snapshot(self):  # noqa: ANN202
            return SimpleNamespace(
                translation=SimpleNamespace(connection="managed"),
                credentials=SimpleNamespace(entries=()),
                managed=SimpleNamespace(
                    trial_remaining_percent=None,
                    referral_id=None,
                    pass_status=None,
                ),
            )

    secret_port = SyncSecretStorePortAdapter(secrets)
    owner = create_production_managed_authentication_application(
        runtime_host=RuntimeHost(),
        ui_settings=UiSettings(),
        secrets=secrets,
        oauth_runtime=OAuthRuntime(),
        canonical_commands=SimpleNamespace(
            secret_commands=CanonicalSecretCommandService(secret_port),
            _secret_port=secret_port,
            runtime_apply=_AppliedRuntime(),
        ),
        persistence=persistence,
        state_path=tmp_path / "settings.json",
    )

    await owner.presentation()
    await owner.presentation()

    assert release.client.calls == 1
    assert "openrouter_managed_delivery_ack_token" not in secrets.values
    assert persistence.receipt.revision == "r3"


@pytest.mark.asyncio
async def test_recovery_rejects_stale_delivery_confirmation_identity(tmp_path: Path) -> None:
    settings = replace(
        AppSettingsVNext(),
        state=replace(
            AppSettingsVNext().state,
            managed_connection=replace(
                AppSettingsVNext().state.managed_connection,
                pending_delivery_ack_source="discord",
                pending_delivery_ack_delivery_id="current-delivery",
                pending_delivery_ack_managed_credential_ref="current-credential",
            ),
        ),
    )
    persistence = _ManagedPersistence(settings)
    secrets = _SyncSecrets()
    secrets.set("openrouter_managed_delivery_ack_token", "current-token")
    secrets.set(
        ack_delivered_secret_key("openrouter_managed_delivery_ack_token"),
        encode_ack_delivery_confirmation("discord", "stale-delivery", "stale-credential"),
    )

    class State:
        pending_delivery_ack_source = "discord"
        pending_delivery_ack_delivery_id = "current-delivery"
        pending_delivery_ack_managed_credential_ref = "current-credential"
        pending_delivery_ack_expires_at = None

    class Client:
        calls = 0

        async def acknowledge_managed_key_delivery(self, request):  # noqa: ANN001, ANN201
            self.calls += 1
            assert request.delivery_ack_token == "current-token"
            return ManagedKeyDeliveryAckResult(True, "acknowledged")

    release = SimpleNamespace(managed_state=State(), client=Client())

    class Host:
        def subscribe_managed_discord_callback(self, _callback):  # noqa: ANN001
            return None

        async def resolve_managed_release_service(self):  # noqa: ANN201
            return release

    class UiSettings:
        async def snapshot(self):  # noqa: ANN201
            return SimpleNamespace(
                translation=SimpleNamespace(connection="managed"),
                credentials=SimpleNamespace(entries=()),
                managed=SimpleNamespace(
                    trial_remaining_percent=None,
                    referral_id=None,
                    pass_status=None,
                ),
            )

    secret_port = SyncSecretStorePortAdapter(secrets)
    owner = create_production_managed_authentication_application(
        runtime_host=Host(),
        ui_settings=UiSettings(),
        secrets=secrets,
        oauth_runtime=OAuthRuntime(),
        canonical_commands=SimpleNamespace(
            secret_commands=CanonicalSecretCommandService(secret_port),
            _secret_port=secret_port,
            runtime_apply=_AppliedRuntime(),
        ),
        persistence=persistence,
        state_path=tmp_path / "settings.json",
    )

    await owner.presentation()

    assert release.client.calls == 1
    assert "openrouter_managed_delivery_ack_token" not in secrets.values


@pytest.mark.asyncio
async def test_full_production_composition_restart_resumes_delivered_ack_cleanup(
    tmp_path: Path,
) -> None:
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        state=replace(
            settings.state,
            managed_connection=replace(
                settings.state.managed_connection,
                pending_delivery_ack_source="discord",
                pending_delivery_ack_delivery_id="delivery",
                pending_delivery_ack_managed_credential_ref="credential",
                pending_delivery_ack_delivered=True,
            ),
        ),
    )
    state_path = tmp_path / "settings.json"
    assert save_vnext_settings(state_path, settings).ok
    composition = create_application_runtime_production_composition(state_path, settings)
    authentication = composition.dashboard._managed_authentication

    await authentication.presentation()

    receipt = await composition.canonical_commands.current_receipt()
    managed = receipt.envelope.state.managed_connection
    assert managed.pending_delivery_ack_source is None
    assert managed.pending_delivery_ack_delivery_id is None
    assert managed.pending_delivery_ack_managed_credential_ref is None
    assert managed.pending_delivery_ack_delivered is False
    assert (
        composition.canonical_commands._secrets.get("openrouter_managed_delivery_ack_token") is None
    )


@pytest.mark.asyncio
async def test_full_production_composition_claim_commits_canonical_values_to_rebound_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "settings.json"
    assert save_vnext_settings(state_path, AppSettingsVNext()).ok
    composition = create_application_runtime_production_composition(
        state_path,
        AppSettingsVNext(),
    )
    canonical = composition.canonical_commands
    before = await canonical.current_receipt()
    rebound = replace(
        before.envelope,
        intent=replace(
            before.envelope.intent,
            secrets=replace(
                before.envelope.intent.secrets,
                backend="encrypted_file",
                encrypted_file_path=str(tmp_path / "managed-secrets.json"),
            ),
        ),
    )
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "composition-passphrase")
    dispatch = await canonical.execute_production_settings_delta(
        before=before.envelope,
        after=rebound,
        expected_revision=before.revision,
        correlation_id="rebind",
    )
    assert dispatch.status == "applied"
    assert dispatch.secrets_rebound is True

    class Listener:
        redirect_uri = "http://127.0.0.1:62187/discord/callback"

        def close(self) -> None:
            return None

    class Client:
        ack_calls = 0

        async def start_discord_oauth(self, **kwargs):  # noqa: ANN003, ANN201
            return ManagedOpenRouterDiscordStartSuccess(
                "https://discord.test/auth",
                kwargs["redirect_uri"],
                "2099-01-01T00:00:00Z",
                "nonce",
                ManagedOpenRouterFingerprintSalt(1, "salt"),
                1,
            )

        async def issue_discord_managed_key(self, _request):  # noqa: ANN001, ANN201
            return ManagedOpenRouterIssueSuccess(
                "managed-composition-key",
                managed_credential_ref="composition-ref",
                expires_at="2099-01-01T00:00:00Z",
                delivery_ack_required=True,
                delivery_id="composition-delivery",
                delivery_ack_token="composition-ack-token",
            )

        async def acknowledge_managed_key_delivery(self, request):  # noqa: ANN001, ANN201
            self.ack_calls += 1
            assert request.delivery_id == "composition-delivery"
            assert request.managed_credential_ref == "composition-ref"
            assert request.delivery_ack_token == "composition-ack-token"
            return ManagedKeyDeliveryAckResult(True, "acknowledged")

    legacy = AppSettings()
    legacy.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    release = ManagedOpenRouterReleaseService(
        openrouter_config=build_openrouter_release_runtime_config(legacy),
        managed_state=ManagedIdentityStateAdapter(legacy, lambda _settings: None),
        secrets=canonical._secrets,
        client=Client(),
        app_version="test",
        raw_hardware_fingerprint_provider=lambda: "hardware",
        discord_oauth_listener_factory=Listener,
        discord_oauth_callback_runner=lambda *_args: asyncio.sleep(0, result=("code", "state")),
    )
    owner = composition.runtime_host._runtime_composition.managed_release_owner
    receipt = await canonical.current_receipt()
    owner.service = release
    owner.receipt = receipt
    owner.signature = owner._signature(
        composition.persistence.legacy_projection(receipt.envelope),
        receipt,
    )
    authentication = composition.dashboard._managed_authentication

    result = await authentication.start_discord(StartDiscordManagedAuthentication("referral"))
    committed = await canonical.current_receipt()

    assert result.status is ManagedAuthenticationStatus.APPLIED
    assert committed.envelope.state.managed_connection.active_managed_credential_ref == (
        "composition-ref"
    )
    assert committed.envelope.state.managed_connection.installation_id
    assert canonical._secrets.get("openrouter_managed_api_key") == ("managed-composition-key")
    assert release.client.ack_calls == 1
    assert canonical._secrets.get("openrouter_managed_delivery_ack_token") is None
    assert committed.envelope.state.managed_connection.pending_delivery_ack_source is None


@pytest.mark.asyncio
async def test_full_production_composition_qq_claim_commits_canonical_values(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "settings.json"
    assert save_vnext_settings(state_path, AppSettingsVNext()).ok
    composition = create_application_runtime_production_composition(
        state_path,
        AppSettingsVNext(),
    )
    canonical = composition.canonical_commands

    class Client:
        async def assert_qq_managed_identity(self, _request):  # noqa: ANN001, ANN201
            return QqManagedAssertionResult(
                True,
                "managed-qq-composition-key",
                QqManagedEntitlementSnapshot(
                    "subject",
                    "qq-composition-ref",
                    "2099-01-01T00:00:00Z",
                ),
                None,
                None,
                None,
                None,
            )

    legacy = AppSettings()
    legacy.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    legacy.translation.connection = TranslationConnection.MANAGED_CHINA
    release = ManagedOpenRouterReleaseService(
        openrouter_config=build_openrouter_release_runtime_config(legacy),
        managed_state=ManagedIdentityStateAdapter(legacy, lambda _settings: None),
        secrets=canonical._secrets,
        client=Client(),
        app_version="test",
    )
    owner = composition.runtime_host._runtime_composition.managed_release_owner
    receipt = await canonical.current_receipt()
    owner.service = release
    owner.receipt = receipt
    owner.signature = owner._signature(
        composition.persistence.legacy_projection(receipt.envelope),
        receipt,
    )
    authentication = composition.dashboard._managed_authentication

    result = await authentication.start_qq(
        StartQqManagedAuthentication(
            "42",
            EphemeralSecretLease.from_text("credential"),
        )
    )
    committed = await canonical.current_receipt()

    assert result.status is ManagedAuthenticationStatus.APPLIED
    assert committed.envelope.state.managed_connection.active_managed_credential_ref == (
        "qq-composition-ref"
    )
    assert canonical._secrets.get("openrouter_managed_qq_api_key") == ("managed-qq-composition-key")


class _SyncSecrets:
    def __init__(self) -> None:
        self.values = {}

    def get(self, key):  # noqa: ANN001, ANN201
        return self.values.get(key)

    def set(self, key, value):  # noqa: ANN001
        self.values[key] = value

    def delete(self, key):  # noqa: ANN001
        self.values.pop(key, None)

    def compare_and_clear(self, key, expected_revision):  # noqa: ANN001, ANN201
        value = self.values.get(key)
        if value is None:
            return "absent"
        revision = hashlib.sha256(value.encode()).hexdigest()
        if revision != expected_revision:
            return "stale"
        self.values.pop(key, None)
        return "cleared"


class _ManagedPersistence:
    def __init__(self, settings: AppSettingsVNext | None = None) -> None:
        self.receipt = SettingsCommitReceipt(settings or AppSettingsVNext(), "r1", "before", None)
        self.saves = 0

    def load_receipt(self, _path, *, reason, correlation_id):  # noqa: ANN001, ANN201
        return self.receipt

    def values_for(self, envelope):  # noqa: ANN001, ANN201
        return asdict(envelope)

    def envelope_from_values(self, values):  # noqa: ANN001, ANN201
        return serialization.from_dict(_mutable_settings(values))

    def persist_delta(
        self,
        _path,
        *,
        baseline,
        next_settings,
        expected_revision,
        reason,
        correlation_id,
    ):  # noqa: ANN001, ANN201
        assert expected_revision == self.receipt.revision
        self.saves += 1
        self.receipt = SettingsCommitReceipt(
            next_settings,
            f"r{self.saves + 1}",
            reason,
            correlation_id,
        )
        return self.receipt


class _AppliedRuntime:
    async def apply_runtime(self, _request):  # noqa: ANN001, ANN201
        return RuntimeApplyResult("applied", None, None)


def _mutable_settings(value):  # noqa: ANN001, ANN201
    if isinstance(value, dict):
        return {key: _mutable_settings(item) for key, item in value.items()}
    if hasattr(value, "items"):
        return {key: _mutable_settings(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_mutable_settings(item) for item in value]
    return value
