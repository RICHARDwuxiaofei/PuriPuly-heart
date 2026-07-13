from __future__ import annotations

from collections.abc import Awaitable, Callable

from puripuly_heart.core.runtime.clipboard import ClipboardRuntime
from puripuly_heart.core.runtime.local_stt_download import LocalSTTDownloadRuntime
from puripuly_heart.core.runtime.mic_test import MicTestRuntime


class ApplicationAdapterLifecycle:
    def __init__(self) -> None:
        self.microphone_test = MicTestRuntime()
        self.local_stt_download = LocalSTTDownloadRuntime()
        self._clipboard: ClipboardRuntime | None = None
        self._closed: set[str] = set()

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

    async def close(self) -> None:
        failures: list[BaseException] = []
        resources = (
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
