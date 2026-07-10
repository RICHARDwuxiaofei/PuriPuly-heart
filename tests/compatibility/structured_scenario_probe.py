from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path


async def _stale_completion_trace() -> dict[str, str]:
    from puripuly_heart.core.clock import FakeClock
    from puripuly_heart.core.orchestrator.hub import ClientHub
    from puripuly_heart.domain.events import STTSessionState, STTSessionStateEvent
    from tests.helpers.fakes import RecordingOscQueue

    class QueueSTT:
        def __init__(self) -> None:
            self.queue: asyncio.Queue[object | None] = asyncio.Queue()

        async def events(self):
            while True:
                event = await self.queue.get()
                if event is None:
                    return
                yield event

        async def close(self) -> None:
            await self.queue.put(None)

        async def emit(self) -> None:
            await self.queue.put(STTSessionStateEvent(state=STTSessionState.STREAMING))

    old = QueueSTT()
    replacement = QueueSTT()
    hub = ClientHub(stt=old, llm=None, osc=RecordingOscQueue(), clock=FakeClock())
    await hub.start(auto_flush_osc=False)
    await hub.replace_stt_provider(replacement)
    await old.emit()
    await asyncio.sleep(0)
    after_replacement = "rejected" if hub.ui_events.empty() else "accepted"
    await hub.stop()
    await replacement.emit()
    await asyncio.sleep(0)
    after_shutdown = "rejected" if hub.ui_events.empty() else "accepted"
    return {
        "after_replacement": after_replacement,
        "after_shutdown": after_shutdown,
    }


def _prompt_trace() -> dict[str, str]:
    from puripuly_heart.config import prompts

    with tempfile.TemporaryDirectory(prefix="puripuly-scenario-prompt-") as directory:
        root = Path(directory)
        files = {
            "probe.md": "NAME_MD",
            "probe.txt": "NAME_TXT",
            "default.md": "DEFAULT_MD",
            "default.txt": "DEFAULT_TXT",
            "translation_prompt.md": "SHARED_TRANSLATION",
        }
        for name, value in files.items():
            (root / name).write_text(value, encoding="utf-8")
        previous = os.environ.get("PURIPULY_HEART_PROMPTS_DIR")
        os.environ["PURIPULY_HEART_PROMPTS_DIR"] = str(root)
        try:
            observed = []
            for name in ("probe.md", "probe.txt", "default.md", "default.txt"):
                prompts._reset_prompt_cache_for_tests()
                observed.append(prompts.load_prompt("probe"))
                (root / name).unlink()
            prompts._reset_prompt_cache_for_tests()
            cerebras = prompts.load_prompt_for_provider("cerebras")
        finally:
            prompts._reset_prompt_cache_for_tests()
            if previous is None:
                os.environ.pop("PURIPULY_HEART_PROMPTS_DIR", None)
            else:
                os.environ["PURIPULY_HEART_PROMPTS_DIR"] = previous
    return {
        "fallback_order": (
            "name_md_name_txt_default_md_default_txt"
            if observed == ["NAME_MD", "NAME_TXT", "DEFAULT_MD", "DEFAULT_TXT"]
            else "unexpected"
        ),
        "cerebras": (
            "shared_translation_prompt" if cerebras == "SHARED_TRANSLATION" else "unexpected"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    sys.path.insert(0, str(source_root))
    sys.path.insert(0, str(source_root / "src"))
    print(
        json.dumps(
            {
                "lifecycle_races_stale_result": asyncio.run(_stale_completion_trace()),
                "prompt_fallback": _prompt_trace(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
