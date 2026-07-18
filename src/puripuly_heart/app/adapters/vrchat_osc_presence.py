from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from puripuly_heart.core.osc.vrchat_osc_presence import (
    VrchatOscPresence,
    probe_vrchat_osc_presence,
)


@dataclass(frozen=True, slots=True)
class PsutilVrchatOscPresenceAdapter:
    probe: Callable[..., VrchatOscPresence] = probe_vrchat_osc_presence

    async def should_prompt_enable_osc(self, *, port: int) -> bool | None:
        presence = await asyncio.to_thread(self.probe, port=port)
        return presence.should_prompt_enable_osc
