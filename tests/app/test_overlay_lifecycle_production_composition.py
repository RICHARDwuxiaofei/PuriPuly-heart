from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from puripuly_heart.app.adapters.overlay_lifecycle_production import (
    ProductionOverlayLifecycleFactories,
    _initial_desktop_controls,
    resolve_overlay_lifecycle_configuration,
)
from puripuly_heart.app.adapters.overlay_runtime_effects import ProductionVrcMicrophoneEffects
from puripuly_heart.app.ports.post_commit_runtime import OverlayOscDirective
from puripuly_heart.app.services.overlay_osc_application_runtime import (
    OverlayLifecycleConfiguration,
)
from puripuly_heart.app.wiring_composition import (
    create_overlay_osc_application_composition,
    create_overlay_production_composition,
)
from puripuly_heart.config.overlay_calibration import OverlayCalibration
from puripuly_heart.config.resolved import DESKTOP_OVERLAY_SIZE_PRESETS
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


@dataclass
class _Process:
    terminated: bool = False

    async def next_event(self) -> dict[str, object]:
        return {"type": "overlay_ready"}

    async def wait(self) -> int | None:
        await asyncio.Future()

    async def terminate(self) -> None:
        self.terminated = True


@dataclass
class _Runner:
    process: _Process

    def prepare(self, manifest) -> Path:  # noqa: ANN001
        return Path("fake-overlay.exe")

    async def spawn(self, executable_path: Path, manifest_path: Path) -> _Process:
        return self.process


def _directive(target: str) -> OverlayOscDirective:
    return OverlayOscDirective(
        "overlay_osc",
        target,
        True,
        False,
        "127.0.0.1",
        9000,
        "/chatbox/input",
        True,
        False,
        144,
        False,
        True,
        OverlayCalibration(offset_x=0.25),
        {
            "position": {"x": 10, "y": 20},
            "size": {"width": 700, "height": 180},
            "visual": {"text_scale": 1.2},
            "interaction_mode": "locked",
        },
    )


async def _connected(runtime) -> None:  # noqa: ANN001
    for _ in range(100):
        if runtime.lifecycle_snapshot().state == "connected":
            return
        await asyncio.sleep(0.01)
    raise AssertionError(runtime.lifecycle_snapshot().state)


@pytest.mark.asyncio
async def test_production_owner_starts_switches_target_and_shuts_down() -> None:
    processes: list[_Process] = []
    targets: list[str] = []

    def runners(target, task_factory):  # noqa: ANN001, ARG001
        targets.append(target)
        process = _Process()
        processes.append(process)
        return _Runner(process)

    configuration = OverlayLifecycleConfiguration(
        True, _directive("desktop"), "ja", "detailed", 500
    )
    hub = SimpleNamespace(overlay_sink=None, overlay_diagnostics=None)
    snapshots = []
    output = SimpleNamespace(publish_overlay_snapshot=snapshots.append)
    runtime, _transactions = create_overlay_osc_application_composition(
        configuration=configuration,
        hub=hub,
        lifecycle_output=output,
        lifecycle_factories=ProductionOverlayLifecycleFactories(runners),
    )

    await runtime.startup()
    await _connected(runtime)
    first_presenter = hub.overlay_sink
    assert targets == ["desktop"]
    assert first_presenter.calibration.offset_x == 0.25

    switched = OverlayLifecycleConfiguration(True, _directive("steamvr"), "ja", "detailed", 500)
    assert await runtime.apply_configuration(switched) is True
    await _connected(runtime)
    assert targets == ["desktop", "steamvr"]
    assert hub.overlay_sink is first_presenter
    assert processes[0].terminated is True

    await runtime.shutdown()
    assert hub.overlay_sink is None
    assert processes[1].terminated is True
    assert snapshots[-1].state == "off"


def test_production_adapter_has_no_ui_or_controller_import() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "puripuly_heart"
        / "app"
        / "adapters"
        / "overlay_lifecycle_production.py"
    ).read_text(encoding="utf-8")
    assert "puripuly_heart.ui" not in source
    assert "GuiController" not in source


def test_desktop_initial_controls_emit_launch_diagnostics_only_in_detailed_mode() -> None:
    options = {
        "size_preset": "medium",
        "size": {"width": 1344, "height": 336},
        "position": {"x": 597, "y": 1017},
        "locked": True,
        "visual": {"text_scale": 1.0, "background_alpha": 0.5},
        "interaction_mode": "edit",
    }
    controls = _initial_desktop_controls(options)
    assert controls[-1] == {"command": "set_interaction_mode", "mode": "edit"}
    assert "bounds_epoch" not in controls[0]


def test_desktop_initial_controls_can_be_built_from_resolved_overlay_config() -> None:
    controls = _initial_desktop_controls(
        {
            "size_preset": "medium",
            "size": {"width": 1344, "height": 336},
            "position": {"x": 597, "y": 1017},
            "locked": True,
            "visual": {
                "text_scale": 1.0,
                "background_alpha": 0.5,
                "outline_width": None,
            },
            "interaction_mode": "edit",
        }
    )
    assert controls == [
        {"command": "apply_window_bounds", "x": 597, "y": 1017, "width": 1344, "height": 336},
        {
            "command": "apply_visual_config",
            "text_scale": 1.0,
            "background_alpha": 0.5,
            "outline_width": None,
        },
        {"command": "set_interaction_mode", "mode": "edit"},
    ]


@pytest.mark.parametrize(
    ("overlay_target", "expected_refresh_burst"),
    [("desktop", False), ("steamvr", True)],
)
def test_overlay_start_logs_selected_target_refresh_flags_for_experiment_boundaries(
    caplog: pytest.LogCaptureFixture,
    overlay_target: str,
    expected_refresh_burst: bool,
) -> None:
    factories = ProductionOverlayLifecycleFactories()
    configuration = OverlayLifecycleConfiguration(
        True, _directive(overlay_target), "en", "detailed", 500
    )
    with caplog.at_level("INFO"):
        factories.create_presenter(
            configuration=configuration,
            diagnostics=factories.create_diagnostics(overlay_instance_id="overlay-test"),
            task_factory=asyncio.create_task,
        )
    message = caplog.messages[-1]
    assert f"target={overlay_target}" in message
    assert "logging_mode=detailed" in message
    assert f"peer_presentation_refresh_burst={expected_refresh_burst}" in message
    assert f"self_presentation_refresh_burst={expected_refresh_burst}" in message


@pytest.mark.parametrize(
    ("preset", "dimensions"),
    list(DESKTOP_OVERLAY_SIZE_PRESETS.items()),
)
def test_resolve_overlay_lifecycle_configuration_uses_all_canonical_desktop_sizes(
    preset: str,
    dimensions: tuple[int, int],
) -> None:
    settings = AppSettingsVNext()
    desktop = replace(settings.intent.overlay.desktop_flet, size_preset=preset)
    overlay = replace(settings.intent.overlay, desktop_flet=desktop)
    settings = replace(settings, intent=replace(settings.intent, overlay=overlay))

    configuration = resolve_overlay_lifecycle_configuration(settings)

    assert configuration.directive.desktop_overlay_options["size"] == {
        "width": dimensions[0],
        "height": dimensions[1],
    }


def test_production_composition_accepts_explicit_vrc_effects_port() -> None:
    gate = object()
    vrc = SimpleNamespace(gate=gate, apply_vrc_microphone_intercept=lambda enabled: enabled)

    composition = create_overlay_production_composition(vrc_microphone=vrc)

    assert composition.vrc is vrc
    assert composition.runtime.vrc_microphone is vrc
    assert composition.audio_gate is gate


@pytest.mark.asyncio
async def test_production_graph_uses_one_vrc_receiver_and_shared_capture_gate() -> None:
    class ReceiverRuntime:
        def __init__(self) -> None:
            self.receiver = None
            self.starts = 0
            self.closes = 0

        async def start(self) -> object:
            self.starts += 1
            self.receiver = object()
            return self.receiver

        async def stop(self) -> None:
            self.receiver = None

        async def close(self) -> None:
            self.closes += 1
            self.receiver = None

    receiver = ReceiverRuntime()
    vrc = ProductionVrcMicrophoneEffects(receiver=receiver)  # type: ignore[arg-type]
    configuration = OverlayLifecycleConfiguration(False, _directive("steamvr"), "en", "basic", 500)
    composition = create_overlay_production_composition(
        configuration=configuration,
        vrc_microphone=vrc,
    )
    assert composition.audio_gate is vrc.gate

    await composition.commands.startup()
    updated = _directive("steamvr")
    object.__setattr__(updated, "vrc_mic_intercept", True)
    assert await composition.runtime.apply_overlay_osc(updated) is True

    assert receiver.starts == 1
    assert composition.audio_gate is vrc.gate
    assert vrc.gate.enabled is True
    assert vrc.gate.receiver_active is True

    await composition.commands.shutdown()
    await composition.commands.shutdown()
    assert receiver.closes == 1
    assert vrc.gate.enabled is False
