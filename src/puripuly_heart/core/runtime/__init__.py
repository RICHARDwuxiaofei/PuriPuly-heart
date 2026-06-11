from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puripuly_heart.core.runtime.clipboard import ClipboardRuntime
    from puripuly_heart.core.runtime.local_stt_download import LocalSTTDownloadRuntime
    from puripuly_heart.core.runtime.mic_test import MicTestRuntime
    from puripuly_heart.core.runtime.oauth import OAuthRuntime
    from puripuly_heart.core.runtime.output import OutputRuntime
    from puripuly_heart.core.runtime.peer_channel import (
        PeerChannelRuntime,
        PeerChannelRuntimeState,
        PeerRuntimeConfig,
        SpeechChannelRuntime,
    )

__all__ = [
    "PeerChannelRuntime",
    "PeerChannelRuntimeState",
    "PeerRuntimeConfig",
    "ClipboardRuntime",
    "LocalSTTDownloadRuntime",
    "MicTestRuntime",
    "OAuthRuntime",
    "OutputRuntime",
    "SpeechChannelRuntime",
]


def __getattr__(name: str) -> object:
    if name in __all__:
        if name == "ClipboardRuntime":
            from puripuly_heart.core.runtime import clipboard

            return getattr(clipboard, name)
        if name == "LocalSTTDownloadRuntime":
            from puripuly_heart.core.runtime import local_stt_download

            return getattr(local_stt_download, name)
        if name == "MicTestRuntime":
            from puripuly_heart.core.runtime import mic_test

            return getattr(mic_test, name)
        if name == "OAuthRuntime":
            from puripuly_heart.core.runtime import oauth

            return getattr(oauth, name)
        if name == "OutputRuntime":
            from puripuly_heart.core.runtime import output

            return getattr(output, name)
        from puripuly_heart.core.runtime import peer_channel

        return getattr(peer_channel, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
