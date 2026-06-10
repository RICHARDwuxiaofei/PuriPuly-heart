from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    "OutputRuntime",
    "SpeechChannelRuntime",
]


def __getattr__(name: str) -> object:
    if name in __all__:
        if name == "OutputRuntime":
            from puripuly_heart.core.runtime import output

            return getattr(output, name)
        from puripuly_heart.core.runtime import peer_channel

        return getattr(peer_channel, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
