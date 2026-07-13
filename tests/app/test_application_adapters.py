from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.app.services.application_adapters import ApplicationAdapterLifecycle


@pytest.mark.asyncio
async def test_application_adapters_close_each_owned_runtime_exactly_once() -> None:
    owner = ApplicationAdapterLifecycle()
    calls: list[str] = []

    class Resource:
        async def close(self) -> None:
            calls.append("close")

    owner.microphone_test = Resource()
    owner.local_stt_download = Resource()
    owner._clipboard = Resource()

    await owner.close()
    await owner.close()

    assert calls == ["close"] * 3


@pytest.mark.asyncio
async def test_application_adapters_aggregate_and_retry_only_failed_close() -> None:
    owner = ApplicationAdapterLifecycle()
    calls = {"retry": 0, "ok": 0}

    class Retry:
        async def close(self) -> None:
            calls["retry"] += 1
            if calls["retry"] == 1:
                raise RuntimeError("retry")

    class Ok:
        async def close(self) -> None:
            calls["ok"] += 1

    owner.microphone_test = Retry()
    owner.local_stt_download = Ok()

    with pytest.raises(RuntimeError, match="retry"):
        await owner.close()
    await owner.close()

    assert calls == {"retry": 2, "ok": 1}


def test_production_composes_adapter_owner_outside_ui_and_controller_does_not_close_it() -> None:
    root = Path(__file__).parents[2] / "src" / "puripuly_heart"
    main_source = (root / "main.py").read_text(encoding="utf-8")
    lifecycle_source = (root / "app" / "services" / "application_lifecycle.py").read_text(
        encoding="utf-8"
    )
    app_source = (root / "ui" / "app.py").read_text(encoding="utf-8")
    controller_path = root / "ui" / "controller.py"

    assert '"application_adapters"' in main_source
    assert "ApplicationAdapterLifecycle," in main_source
    assert "adopt_application_adapters" in main_source
    assert "GithubStarPromptRuntime(" not in app_source
    assert "OAuthRuntime(" not in app_source
    assert not controller_path.exists()
    assert "controller.stop" not in main_source
    assert "stop_legacy_application_adapters" not in lifecycle_source
