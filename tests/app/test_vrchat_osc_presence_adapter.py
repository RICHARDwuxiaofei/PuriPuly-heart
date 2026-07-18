from __future__ import annotations

import pytest

from puripuly_heart.app.adapters.vrchat_osc_presence import (
    PsutilVrchatOscPresenceAdapter,
)
from puripuly_heart.core.osc.vrchat_osc_presence import VrchatOscPresence


@pytest.mark.asyncio
async def test_adapter_reports_probe_decision() -> None:
    ports: list[int] = []

    def probe(*, port: int) -> VrchatOscPresence:
        ports.append(port)
        return VrchatOscPresence(vrchat_running=True, osc_listening=False)

    adapter = PsutilVrchatOscPresenceAdapter(probe=probe)

    assert await adapter.should_prompt_enable_osc(port=9012) is True
    assert ports == [9012]
