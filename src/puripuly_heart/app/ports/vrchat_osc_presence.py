from __future__ import annotations

from typing import Protocol


class VrchatOscPresencePort(Protocol):
    async def should_prompt_enable_osc(self, *, port: int) -> bool | None: ...
