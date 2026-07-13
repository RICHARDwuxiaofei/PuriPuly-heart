from __future__ import annotations

import webbrowser
from datetime import datetime, timezone

from puripuly_heart.app.ports.managed_authentication_application import (
    ManagedAuthenticationBrowserPort,
    ManagedAuthenticationPresentation,
)
from puripuly_heart.app.services.canonical_secret_commands import SyncSecretStorePortAdapter
from puripuly_heart.app.services.managed_auth_claims import ManagedAuthClaimGuard
from puripuly_heart.app.services.managed_authentication_application import (
    ManagedAuthenticationApplication,
    managed_authentication_presentation,
)
from puripuly_heart.app.services.qq_managed_auth import QqManagedAuthRequest, QqManagedAuthService


class ProductionManagedAuthenticationBrowser(ManagedAuthenticationBrowserPort):
    def __init__(self, oauth_runtime) -> None:  # noqa: ANN001
        self._oauth_runtime = oauth_runtime
        self._authorization_url: str | None = None

    @property
    def available(self) -> bool:
        return self._authorization_url is not None

    def set_authorization_url(self, url: str | None) -> None:
        self._authorization_url = url

    async def reopen(self) -> bool:
        return bool(self._authorization_url and webbrowser.open(self._authorization_url))

    async def cancel(self) -> None:
        await self._oauth_runtime.cancel_auth_task("managed-discord")
        await self._oauth_runtime.cancel_auth_task("managed-qq")
        self._authorization_url = None


def create_production_managed_authentication_application(
    *, runtime_host, ui_settings, secrets, oauth_runtime
) -> ManagedAuthenticationApplication:  # noqa: ANN001
    secret_port = SyncSecretStorePortAdapter(secrets)
    browser = ProductionManagedAuthenticationBrowser(oauth_runtime)
    browser_state: dict[str, object] = {
        "authorization_url": None,
        "callback_received": False,
        "referral_bonus_applied": False,
    }
    qq_service: QqManagedAuthService | None = None
    qq_release: object | None = None
    application: ManagedAuthenticationApplication | None = None

    def callback_received(_event: object) -> None:
        browser_state["callback_received"] = True
        if application is not None:
            application.set_callback_received()

    runtime_host.subscribe_managed_discord_callback(callback_received)

    async def presentation() -> ManagedAuthenticationPresentation:
        snapshot = await ui_settings.snapshot()
        connection = snapshot.translation.connection or ""
        selected = connection in {"managed", "managed_china"}
        managed_key = next(
            (
                entry
                for entry in snapshot.credentials.entries
                if entry.key == "openrouter_managed_api_key"
            ),
            None,
        )
        available = bool(managed_key is not None and managed_key.present)
        return managed_authentication_presentation(
            action="continue" if not selected or available else "prompt",
            prompt="qq" if connection == "managed_china" else "discord",
            connection_state="connected" if available else "disconnected",
            browser_reopen_available=browser.available,
            referral_bonus_applied=bool(browser_state["referral_bonus_applied"]),
            trial_remaining_percent=snapshot.managed.trial_remaining_percent,
            referral_id=snapshot.managed.referral_id,
            pass_status=snapshot.managed.pass_status,
            callback_received=bool(browser_state["callback_received"]),
        )

    async def start_discord(referral_id: str | None) -> tuple[str, str | None]:
        service = await runtime_host.resolve_managed_release_service()
        if service is None:
            return "unavailable", "managed_release_unavailable"
        browser_state["callback_received"] = False
        original_runner = service.discord_oauth_callback_runner

        async def callback_runner(listener, authorization_url, expires_at):  # noqa: ANN001, ANN202
            browser_state["authorization_url"] = authorization_url
            browser.set_authorization_url(authorization_url)
            if application is not None:
                application.set_browser_reopen_available(True)
            return await original_runner(listener, authorization_url, expires_at)

        service.discord_oauth_callback_runner = callback_runner
        try:
            result = await service.prepare_for_translation(referral_id=referral_id)
        finally:
            service.discord_oauth_callback_runner = original_runner
        browser_state["referral_bonus_applied"] = bool(
            getattr(result, "referral_bonus_applied", False)
        )
        if bool(getattr(result, "succeeded", False)):
            return "applied", None
        return "rejected", getattr(result, "message_key", "discord_auth.error.retry")

    async def start_qq(identity: str, credential: str) -> tuple[str, str | None]:
        nonlocal qq_release, qq_service
        release = await runtime_host.resolve_managed_release_service()
        if release is None or getattr(release, "client", None) is None:
            return "unavailable", "managed_release_unavailable"
        if qq_service is None or qq_release is not release:
            managed_state = release.managed_state
            qq_service = QqManagedAuthService(
                broker_client=release.client,
                secret_store=secret_port,
                managed_state=managed_state,
                claim_guard=ManagedAuthClaimGuard(managed_state, secret_port),
            )
            qq_release = release
        result = await qq_service.authenticate(
            QqManagedAuthRequest(
                identity,
                credential,
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                metadata={"flow": "dashboard"},
            )
        )
        detail = None if result.message is None else result.message.key
        return (
            (
                "applied"
                if getattr(result, "status", "failed") in {"applied", "committed"}
                else "rejected"
            ),
            detail,
        )

    async def cancel() -> None:
        browser_state["authorization_url"] = None
        if application is not None:
            application.set_browser_reopen_available(False)

    application = ManagedAuthenticationApplication(
        presentation=presentation,
        start_discord=start_discord,
        start_qq=start_qq,
        browser=browser,
        close_authentication=cancel,
        oauth_runtime=oauth_runtime,
    )
    return application


__all__ = [
    "ProductionManagedAuthenticationBrowser",
    "create_production_managed_authentication_application",
]
