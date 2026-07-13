from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.app.adapters.overlay_osc_runtime import (
    OverlayOscRuntimeSynchronization,
)
from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.post_commit_runtime import (
    DashboardRetryFactsDirective,
    OverlayOscDirective,
)
from puripuly_heart.app.services.overlay_osc_application_runtime import (
    OverlayOscApplicationRuntime,
)
from puripuly_heart.app.wiring_composition import (
    create_overlay_osc_application_composition,
)
from puripuly_heart.config.overlay_calibration import OverlayCalibration
from puripuly_heart.core.messages import (
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_FAILED,
)


def _request() -> ResolvedRuntimeActivationRequest:
    return ResolvedRuntimeActivationRequest(
        object(), "revision", "reason", "correlation"  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_production_composition_owns_start_apply_dashboard_and_shutdown() -> None:
    commands, transactions = create_overlay_osc_application_composition()
    runtime = transactions.owner.synchronization.runtime
    assert commands.runtime is runtime

    await runtime.startup()
    directive = OverlayOscDirective(
        "overlay_osc",
        "desktop",
        False,
        False,
        "127.0.0.1",
        9000,
        "/chatbox/input",
        True,
        False,
        144,
        False,
        True,
        OverlayCalibration(offset_x=0.2),
        {"size_preset": "large"},
    )
    sync = OverlayOscRuntimeSynchronization(runtime)
    result = await sync.synchronize_runtime(_request(), directive)
    published = []

    class Dashboard:
        def publish_dashboard_runtime_facts(self, value):  # noqa: ANN001
            published.append(value)

    runtime.dashboard = Dashboard()
    facts = DashboardRetryFactsDirective(
        "dashboard_retry_facts", False, True, False, True, False, True
    )
    facts_result = await sync.synchronize_runtime(_request(), facts)

    assert result.status == facts_result.status == RUNTIME_APPLY_STATUS_APPLIED
    assert runtime.snapshot().directive is directive
    assert runtime.snapshot().dashboard_facts is facts
    assert published == [facts]
    await runtime.shutdown()
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_unsupported_directive_fails_honestly() -> None:
    runtime = OverlayOscApplicationRuntime()
    result = await OverlayOscRuntimeSynchronization(runtime).synchronize_runtime(
        _request(), object()
    )
    assert result.status == RUNTIME_APPLY_STATUS_FAILED


@pytest.mark.asyncio
async def test_dashboard_retry_facts_remain_observable_in_runtime_snapshot_without_consumer() -> (
    None
):
    runtime = OverlayOscApplicationRuntime()
    facts = DashboardRetryFactsDirective(
        "dashboard_retry_facts", False, True, False, True, False, True
    )
    result = await OverlayOscRuntimeSynchronization(runtime).synchronize_runtime(_request(), facts)
    assert result.status != RUNTIME_APPLY_STATUS_APPLIED
    assert runtime.snapshot().dashboard_facts is facts


def test_application_overlay_runtime_has_no_ui_or_controller_dependency() -> None:
    root = Path(__file__).parents[2] / "src" / "puripuly_heart"
    source = (root / "app" / "services" / "overlay_osc_application_runtime.py").read_text(
        encoding="utf-8"
    )
    source += (root / "app" / "adapters" / "overlay_osc_runtime.py").read_text(encoding="utf-8")
    assert "puripuly_heart.ui" not in source
    assert "GuiController" not in source
    ui_source = (root / "ui" / "app.py").read_text(encoding="utf-8")
    assert "create_overlay_osc" not in ui_source
    assert "surface_runtime_factory" not in ui_source
