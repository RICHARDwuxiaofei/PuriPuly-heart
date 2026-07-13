from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from puripuly_heart.app.adapters.managed_authentication_production import (
    ProductionManagedAuthenticationBrowser,
    create_production_managed_authentication_application,
)
from puripuly_heart.app.ports.managed_authentication_application import (
    EphemeralSecretLease,
    ManagedAuthenticationPrompt,
    ManagedAuthenticationStatus,
    StartDiscordManagedAuthentication,
    StartQqManagedAuthentication,
)
from puripuly_heart.app.services.managed_authentication_application import (
    ManagedAuthenticationApplication,
    managed_authentication_presentation,
)
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
) -> None:
    callbacks = []
    opened_urls: list[str] = []

    class Runtime:
        close_count = 0

        async def close(self) -> None:
            self.close_count += 1

    runtime = Runtime()

    class Release:
        oauth_runtime = runtime

        async def discord_oauth_callback_runner(
            self, listener, authorization_url, expires_at
        ):  # noqa: ANN001, ANN202
            callbacks[0](object())

        async def prepare_for_translation(self, *, referral_id):  # noqa: ANN001, ANN202
            assert referral_id == "referral"
            await self.discord_oauth_callback_runner(None, "https://discord.test/auth", None)
            return SimpleNamespace(succeeded=True, referral_bonus_applied=True)

    release = Release()

    class RuntimeHost:
        managed_release_service = release

        def subscribe_managed_discord_callback(self, callback):  # noqa: ANN001, ANN202
            callbacks.append(callback)

        async def resolve_managed_release_service(self):  # noqa: ANN202
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
    owner = create_production_managed_authentication_application(
        runtime_host=RuntimeHost(),
        ui_settings=UiSettings(),
        secrets=SimpleNamespace(),
        oauth_runtime=OAuthRuntime(),
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
    assert runtime.close_count == 0
    assert cancelled_presentation.browser_reopen_available is False
