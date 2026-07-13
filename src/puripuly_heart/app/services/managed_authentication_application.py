from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace

from puripuly_heart.app.ports.managed_authentication_application import (
    ManagedAuthenticationBrowserPort,
    ManagedAuthenticationPresentation,
    ManagedAuthenticationPrompt,
    ManagedAuthenticationResult,
    ManagedAuthenticationStatus,
    StartDiscordManagedAuthentication,
    StartQqManagedAuthentication,
)


class ManagedAuthenticationApplication:
    def __init__(
        self,
        *,
        presentation: Callable[[], Awaitable[ManagedAuthenticationPresentation]],
        start_discord: Callable[[str | None], Awaitable[tuple[str, str | None]]],
        start_qq: Callable[[str, str], Awaitable[tuple[str, str | None]]],
        browser: ManagedAuthenticationBrowserPort,
        close_authentication: Callable[[], Awaitable[None]],
        oauth_runtime,
    ) -> None:
        self._presentation = presentation
        self._start_discord = start_discord
        self._start_qq = start_qq
        self._browser = browser
        self._close_authentication = close_authentication
        self._oauth_runtime = oauth_runtime
        self._in_progress = False
        self._generation = 0
        self._listeners: list[Callable[[ManagedAuthenticationPresentation], None]] = []
        self._last_presentation: ManagedAuthenticationPresentation | None = None

    def subscribe_presentation(
        self, listener: Callable[[ManagedAuthenticationPresentation], None]
    ) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    async def presentation(self) -> ManagedAuthenticationPresentation:
        presentation = await self._presentation()
        if self._in_progress:
            presentation = replace(presentation, action="in_progress", generation=self._generation)
        self._last_presentation = presentation
        return presentation

    async def start_discord(
        self, command: StartDiscordManagedAuthentication
    ) -> ManagedAuthenticationResult:
        self._begin()
        self._publish(await self.presentation())
        try:
            task = self._oauth_runtime.create_auth_task(
                self._start_discord(command.referral_id), task_name="managed-discord"
            )
            status, detail = await task
            return await self._result(status, detail)
        finally:
            self.clear_pending()

    async def start_qq(self, command: StartQqManagedAuthentication) -> ManagedAuthenticationResult:
        try:
            self._begin()
            self._publish(await self.presentation())
            credential = command.credential.consume()
            task = self._oauth_runtime.create_auth_task(
                self._start_qq(command.qq_identity, credential), task_name="managed-qq"
            )
            status, detail = await task
            return await self._result(status, detail)
        finally:
            command.credential.clear()
            self.clear_pending()

    async def reopen_discord_browser(self) -> ManagedAuthenticationResult:
        reopened = await self._browser.reopen()
        result = await self._result("applied" if reopened else "unavailable", None)
        self._publish(result.presentation)
        return result

    async def cancel(self) -> ManagedAuthenticationResult:
        await self._browser.cancel()
        self.clear_pending()
        result = await self._result("cancelled", None)
        self._publish(result.presentation)
        return result

    def set_callback_received(self) -> None:
        self._replace_and_publish(callback_received=True)

    def set_browser_reopen_available(self, available: bool) -> None:
        self._replace_and_publish(browser_reopen_available=available)

    def _replace_and_publish(self, **changes: object) -> None:
        if self._last_presentation is not None:
            self._last_presentation = replace(self._last_presentation, **changes)
            self._publish(self._last_presentation)

    def _publish(self, presentation: ManagedAuthenticationPresentation) -> None:
        for listener in tuple(self._listeners):
            listener(presentation)

    def _begin(self) -> None:
        self._generation += 1
        self._in_progress = True

    def clear_pending(self) -> None:
        self._in_progress = False

    async def _result(self, status: str, detail: str | None) -> ManagedAuthenticationResult:
        mapped = {
            "applied": ManagedAuthenticationStatus.APPLIED,
            "rejected": ManagedAuthenticationStatus.REJECTED,
            "unavailable": ManagedAuthenticationStatus.UNAVAILABLE,
            "cancelled": ManagedAuthenticationStatus.CANCELLED,
        }.get(status, ManagedAuthenticationStatus.FAILED)
        result = ManagedAuthenticationResult(mapped, await self.presentation(), detail)
        self._publish(result.presentation)
        return result

    async def close(self) -> None:
        await self._browser.cancel()
        await self._oauth_runtime.close()
        await self._close_authentication()
        self._listeners.clear()


def managed_authentication_presentation(
    *,
    action: str,
    prompt: str,
    connection_state: str,
    browser_reopen_available: bool,
    referral_bonus_applied: bool,
    trial_remaining_percent: int | None = None,
    referral_id: str | None = None,
    pass_status: str | None = None,
    generation: int = 0,
    callback_received: bool = False,
) -> ManagedAuthenticationPresentation:
    return ManagedAuthenticationPresentation(
        action=action,
        prompt=(
            ManagedAuthenticationPrompt.QQ
            if prompt == ManagedAuthenticationPrompt.QQ.value
            else ManagedAuthenticationPrompt.DISCORD
        ),
        connection_state=connection_state,
        browser_reopen_available=browser_reopen_available,
        referral_bonus_applied=referral_bonus_applied,
        trial_remaining_percent=trial_remaining_percent,
        referral_id=referral_id,
        pass_status=pass_status,
        generation=generation,
        callback_received=callback_received,
    )


__all__ = ["ManagedAuthenticationApplication", "managed_authentication_presentation"]
