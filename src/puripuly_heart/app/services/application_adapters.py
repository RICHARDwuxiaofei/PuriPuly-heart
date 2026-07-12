from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from puripuly_heart.core.runtime.clipboard import ClipboardRuntime
from puripuly_heart.core.runtime.github_star_prompt import GithubStarPromptRuntime
from puripuly_heart.core.runtime.local_stt_download import LocalSTTDownloadRuntime
from puripuly_heart.core.runtime.mic_test import MicTestRuntime
from puripuly_heart.core.runtime.oauth import OAuthRuntime


class ApplicationAdapterLifecycle:
    def __init__(self) -> None:
        self.ui_oauth = OAuthRuntime()
        self.controller_oauth = OAuthRuntime()
        self.ui_github_prompt = GithubStarPromptRuntime()
        self.controller_github_prompt = GithubStarPromptRuntime()
        self.microphone_test = MicTestRuntime()
        self.local_stt_download = LocalSTTDownloadRuntime()
        self._clipboard: ClipboardRuntime | None = None
        self._closed: set[str] = set()

    def bind_ui_github_diagnostics(self, sink: Callable[[str, Mapping[str, object]], None]) -> None:
        self.ui_github_prompt.diagnostics_sink = sink

    def bind_controller_github_diagnostics(
        self, sink: Callable[[str, Mapping[str, object]], None]
    ) -> None:
        self.controller_github_prompt.diagnostics_sink = sink

    def bind_clipboard(
        self,
        *,
        watcher_factory: Callable[..., object],
        submit_handler: Callable[[str], Awaitable[None]],
    ) -> ClipboardRuntime:
        if self._clipboard is None:
            self._clipboard = ClipboardRuntime(
                watcher_factory=watcher_factory,
                submit_handler=submit_handler,
            )
        return self._clipboard

    @property
    def clipboard(self) -> ClipboardRuntime | None:
        return self._clipboard

    async def cancel_ui_oauth(self) -> None:
        await self.ui_oauth.close()
        self._closed.add("ui_oauth")

    async def cancel_ui_github_prompt(self) -> None:
        await self.ui_github_prompt.close()
        self._closed.add("ui_github_prompt")

    async def close(self) -> None:
        failures: list[BaseException] = []
        resources = (
            ("ui_oauth", self.ui_oauth),
            ("controller_oauth", self.controller_oauth),
            ("ui_github_prompt", self.ui_github_prompt),
            ("controller_github_prompt", self.controller_github_prompt),
            ("clipboard", self._clipboard),
            ("microphone_test", self.microphone_test),
            ("local_stt_download", self.local_stt_download),
        )
        for name, resource in resources:
            if resource is None or name in self._closed:
                continue
            try:
                await resource.close()
            except BaseException as exc:
                failures.append(exc)
            else:
                self._closed.add(name)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("application adapter shutdown failed", failures)


__all__ = ["ApplicationAdapterLifecycle"]
