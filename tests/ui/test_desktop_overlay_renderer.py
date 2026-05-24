from __future__ import annotations

import asyncio
import builtins
import inspect
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import flet as ft
import pytest
import websockets

from puripuly_heart.core.overlay.bridge import OverlayBridge
from puripuly_heart.core.overlay.manifest import OVERLAY_CONTRACT_VERSION, OverlayLaunchManifest
from puripuly_heart.core.overlay.protocol import (
    OverlayPresentationBlock,
    OverlayPresentationCalibration,
    OverlayPresentationSnapshot,
)
from puripuly_heart.ui import desktop_overlay


def _manifest(**overrides: object) -> OverlayLaunchManifest:
    values: dict[str, object] = {
        "contract_version": OVERLAY_CONTRACT_VERSION,
        "app_version": "test",
        "overlay_instance_id": "desktop-overlay-test",
        "bridge_url": "ws://127.0.0.1:8765",
        "session_token": "test-session-token",
        "parent_pid": 1234,
        "startup_deadline_ms": 1000,
        "log_dir": "logs",
        "log_level": "INFO",
        "locale": "en",
        "logging_mode": "basic",
    }
    values.update(overrides)
    return OverlayLaunchManifest(**values)  # type: ignore[arg-type]


def _write_manifest(tmp_path: Path, manifest: OverlayLaunchManifest) -> Path:
    path = tmp_path / "overlay-manifest.json"
    path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    return path


def _block(
    block_id: str,
    *,
    channel: str,
    block_variant: str,
    appearance_seq: int,
    primary_text: str,
    secondary_text: str = "",
    secondary_enabled: bool = False,
) -> OverlayPresentationBlock:
    return OverlayPresentationBlock(
        id=block_id,
        occupant_key=f"{channel}:{block_id}",
        appearance_seq=appearance_seq,
        channel=channel,  # type: ignore[arg-type]
        block_variant=block_variant,  # type: ignore[arg-type]
        primary_text=primary_text,
        secondary_text=secondary_text,
        secondary_enabled=secondary_enabled,
    )


def test_desktop_overlay_snapshot_mapping_table_documents_current_block_contract() -> None:
    rows = {
        (row.snapshot_field, row.block_type, row.slot): row
        for row in desktop_overlay.DESKTOP_CAPTION_MAPPING_TABLE
    }

    assert rows[("blocks[]", "active_self/self", "primary")].role == "active_self_source"
    assert rows[("blocks[]", "active_self/self", "primary")].color == "#FFFFFF"
    assert rows[("blocks[]", "active_self/self", "secondary")].role == "active_self_translation"
    assert rows[("blocks[]", "active_self/self", "secondary")].color == "#FFD700"
    active_peer_row = rows[("blocks[]", "active_peer/peer", "primary")]
    assert active_peer_row.role == "active_peer_source"
    assert active_peer_row.promoted is True
    assert rows[("blocks[]", "finalized/peer translated", "primary")].role == ("peer_translation")
    peer_source_only_row = rows[("blocks[]", "finalized/peer source-only", "primary")]
    assert peer_source_only_row.promoted is True
    assert peer_source_only_row.truncation == (
        "max 2 lines; drops before active and translated primary lines"
    )
    assert rows[("blocks[]", "finalized/self", "secondary")].role == "self_translation"
    self_secondary_only_row = rows[("blocks[]", "finalized/self secondary-only", "primary")]
    assert self_secondary_only_row.role == "self_translation"
    assert self_secondary_only_row.promoted is True
    assert rows[("calibration", "all", "none")].role == "desktop_visual_ignored"
    assert rows[("blocks[]", "none/edit", "none")].role == "edit_no_caption_empty_card"
    assert rows[("blocks[]", "none/edit", "none")].truncation == (
        "renders empty caption card with no text"
    )
    assert rows[("blocks[]", "none/pass_through", "none")].truncation == (
        "renders no text and no background"
    )


def test_desktop_overlay_snapshot_mapping_table_matches_emitted_caption_lines() -> None:
    row_by_block_and_role = {
        (row.block_type, row.role): row for row in desktop_overlay.DESKTOP_CAPTION_MAPPING_TABLE
    }
    cases = [
        (
            "active_self/self",
            _block(
                "self-active",
                channel="self",
                block_variant="active_self",
                appearance_seq=10,
                primary_text="active self source",
                secondary_text="active self translation",
                secondary_enabled=True,
            ),
            ("active self source", "active self translation"),
        ),
        (
            "active_peer/peer",
            _block(
                "peer-active",
                channel="peer",
                block_variant="active_peer",
                appearance_seq=20,
                primary_text="",
                secondary_text="active peer source",
                secondary_enabled=True,
            ),
            ("active peer source",),
        ),
        (
            "finalized/peer translated",
            _block(
                "peer-translated",
                channel="peer",
                block_variant="finalized",
                appearance_seq=30,
                primary_text="peer translation",
                secondary_text="peer original",
                secondary_enabled=True,
            ),
            ("peer translation", "peer original"),
        ),
        (
            "finalized/peer source-only",
            _block(
                "peer-source-only",
                channel="peer",
                block_variant="finalized",
                appearance_seq=40,
                primary_text="",
                secondary_text="peer source only",
                secondary_enabled=True,
            ),
            ("peer source only",),
        ),
        (
            "finalized/self",
            _block(
                "self-finalized",
                channel="self",
                block_variant="finalized",
                appearance_seq=50,
                primary_text="self source",
                secondary_text="self translation",
                secondary_enabled=True,
            ),
            ("self source", "self translation"),
        ),
        (
            "finalized/self secondary-only",
            _block(
                "self-secondary-only",
                channel="self",
                block_variant="finalized",
                appearance_seq=60,
                primary_text="",
                secondary_text="self translation only",
                secondary_enabled=True,
            ),
            ("self translation only",),
        ),
    ]

    for block_type, block, expected_texts in cases:
        plan = desktop_overlay.build_desktop_caption_plan(
            OverlayPresentationSnapshot(blocks=[block])
        )
        assert tuple(line.text for line in plan.lines) == expected_texts
        for line in plan.lines:
            row = row_by_block_and_role[(block_type, line.role)]
            assert row.slot == line.slot, (block_type, line.role)
            assert row.promoted is line.promoted, (block_type, line.role)
            assert row.color == line.color, (block_type, line.role)
            assert int(row.priority.split(maxsplit=1)[0]) == line.priority, (
                block_type,
                line.role,
            )


def test_desktop_overlay_snapshot_mapping_roles_secondary_promotion_and_colors() -> None:
    active_self_plan = desktop_overlay.build_desktop_caption_plan(
        OverlayPresentationSnapshot(
            revision=12,
            blocks=[
                _block(
                    "self-active",
                    channel="self",
                    block_variant="active_self",
                    appearance_seq=10,
                    primary_text="I can hear you",
                    secondary_text="들려요",
                    secondary_enabled=True,
                )
            ],
        )
    )
    peer_translated_plan = desktop_overlay.build_desktop_caption_plan(
        OverlayPresentationSnapshot(
            revision=13,
            blocks=[
                _block(
                    "peer-translated",
                    channel="peer",
                    block_variant="finalized",
                    appearance_seq=11,
                    primary_text="좋아요",
                    secondary_text="Sounds good",
                    secondary_enabled=True,
                )
            ],
        )
    )
    active_peer_plan = desktop_overlay.build_desktop_caption_plan(
        OverlayPresentationSnapshot(
            revision=14,
            blocks=[
                _block(
                    "peer-active",
                    channel="peer",
                    block_variant="active_peer",
                    appearance_seq=12,
                    primary_text="",
                    secondary_text="typing live source",
                    secondary_enabled=True,
                )
            ],
        )
    )

    line_by_text = {
        line.text: line
        for plan in (active_self_plan, peer_translated_plan, active_peer_plan)
        for line in plan.lines
    }
    assert line_by_text["I can hear you"].role == "active_self_source"
    assert line_by_text["I can hear you"].slot == "primary"
    assert line_by_text["I can hear you"].color == "#FFFFFF"
    assert line_by_text["들려요"].role == "active_self_translation"
    assert line_by_text["들려요"].slot == "secondary"
    assert line_by_text["들려요"].color == "#FFD700"
    assert line_by_text["typing live source"].role == "active_peer_source"
    assert line_by_text["typing live source"].slot == "primary"
    assert line_by_text["typing live source"].promoted is True
    assert line_by_text["좋아요"].role == "peer_translation"
    assert line_by_text["좋아요"].color == "#FFD700"
    for plan in (active_self_plan, peer_translated_plan, active_peer_plan):
        assert sum(line.max_lines for line in plan.lines) <= 4


@pytest.mark.parametrize(
    "block",
    [
        _block(
            "active-peer-disabled-secondary",
            channel="peer",
            block_variant="active_peer",
            appearance_seq=1,
            primary_text="",
            secondary_text="disabled active peer source",
            secondary_enabled=False,
        ),
        _block(
            "peer-source-only-disabled-secondary",
            channel="peer",
            block_variant="finalized",
            appearance_seq=2,
            primary_text="",
            secondary_text="disabled peer source only",
            secondary_enabled=False,
        ),
        _block(
            "self-secondary-only-disabled-secondary",
            channel="self",
            block_variant="finalized",
            appearance_seq=3,
            primary_text="",
            secondary_text="disabled self translation only",
            secondary_enabled=False,
        ),
    ],
)
def test_desktop_overlay_caption_rendering_disabled_secondary_only_blocks_do_not_promote(
    block: OverlayPresentationBlock,
) -> None:
    plan = desktop_overlay.build_desktop_caption_plan(OverlayPresentationSnapshot(blocks=[block]))

    assert plan.lines == ()
    assert plan.surface_visible is False


def test_desktop_overlay_caption_rendering_no_caption_states_use_empty_moving_card_and_transparent_locked() -> (
    None
):
    empty_snapshot = OverlayPresentationSnapshot(revision=2, blocks=[])

    edit_plan = desktop_overlay.build_desktop_caption_plan(
        empty_snapshot,
        interaction_mode="edit",
        locale="ja",
    )
    locked_plan = desktop_overlay.build_desktop_caption_plan(
        empty_snapshot,
        interaction_mode="pass_through",
        locale="ja",
    )

    assert edit_plan.lines == ()
    assert edit_plan.surface_visible is True
    assert edit_plan.background_alpha == pytest.approx(0.5)
    assert edit_plan.background_color == "#80000000"
    assert locked_plan.lines == ()
    assert locked_plan.surface_visible is False
    assert locked_plan.background_alpha == 0
    assert locked_plan.background_color == "transparent"


def test_desktop_overlay_visual_config_uses_preset_tokens_and_no_outline_text() -> None:
    snapshot = OverlayPresentationSnapshot(
        blocks=[
            _block(
                "peer-translated",
                channel="peer",
                block_variant="finalized",
                appearance_seq=1,
                primary_text="안녕하세요 👋",
                secondary_text="Hello there 👋",
                secondary_enabled=True,
            )
        ]
    )
    plan = desktop_overlay.build_desktop_caption_plan(
        snapshot,
        window_width=1344,
        window_height=347,
        visual_state=desktop_overlay.DesktopCaptionVisualState(
            text_scale=1.0,
            background_alpha=0.38,
            outline_width=None,
        ),
        locale="ko",
    )

    assert plan.primary_font_size == 43
    assert plan.secondary_font_size == 27
    assert plan.outline_width == 0
    assert plan.background_color == "#61000000"
    assert plan.padding_horizontal == 22
    assert plan.padding_vertical == 12
    assert plan.border_radius == 16

    surface = desktop_overlay.build_desktop_caption_surface(plan)
    assert surface.bgcolor == "#61000000"
    assert surface.padding.left == 22
    assert surface.padding.top == 12
    assert surface.border_radius == 16
    column = surface.content
    assert column.scroll is None
    assert all(isinstance(control, ft.Text) for control in column.controls)
    first_text = column.controls[0]
    assert first_text.color == "#FFD700"
    assert first_text.text_align == ft.TextAlign.CENTER
    assert first_text.overflow == ft.TextOverflow.ELLIPSIS
    assert first_text.style.foreground is None


def test_desktop_overlay_active_captions_do_not_receive_alpha_bonus() -> None:
    snapshot = OverlayPresentationSnapshot(
        blocks=[
            _block(
                "self-active",
                channel="self",
                block_variant="active_self",
                appearance_seq=1,
                primary_text="active source",
            )
        ]
    )

    plan = desktop_overlay.build_desktop_caption_plan(
        snapshot,
        window_width=1344,
        window_height=347,
        visual_state=desktop_overlay.DesktopCaptionVisualState(background_alpha=0.5),
        interaction_mode="edit",
    )

    assert any(line.active for line in plan.lines)
    assert plan.background_alpha == pytest.approx(0.5)
    assert plan.background_color == "#80000000"


def test_desktop_overlay_overflow_prioritizes_newest_active_and_peer_translation() -> None:
    snapshot = OverlayPresentationSnapshot(
        blocks=[
            _block(
                "old-self",
                channel="self",
                block_variant="finalized",
                appearance_seq=1,
                primary_text="older finalized self source",
                secondary_text="older finalized self translation",
                secondary_enabled=True,
            ),
            _block(
                "peer-translated",
                channel="peer",
                block_variant="finalized",
                appearance_seq=2,
                primary_text="newer translated peer result",
                secondary_text="newer peer original",
                secondary_enabled=True,
            ),
            _block(
                "peer-active",
                channel="peer",
                block_variant="active_peer",
                appearance_seq=3,
                primary_text="",
                secondary_text="newest active peer source",
                secondary_enabled=True,
            ),
        ],
    )

    plan = desktop_overlay.build_desktop_caption_plan(snapshot)

    visible_text = [line.text for line in plan.lines]
    assert visible_text == [
        "newer translated peer result",
        "newest active peer source",
    ]
    assert sum(line.max_lines for line in plan.lines) == 4
    assert "older finalized self source" not in visible_text
    assert "older finalized self translation" not in visible_text
    assert plan.overflow_strategy == (
        "four-line-budget:newest-active,peer-translated-primary,drop-secondary-then-older-finalized"
    )


def test_desktop_overlay_caption_rendering_preserves_cjk_emoji_and_minimum_secondary_size() -> None:
    snapshot = OverlayPresentationSnapshot(
        blocks=[
            _block(
                "mixed-script",
                channel="peer",
                block_variant="finalized",
                appearance_seq=1,
                primary_text="今日は PuriPuly Heart 좋아요 😊",
                secondary_text="今日は mixed 원문 😊",
                secondary_enabled=True,
            )
        ]
    )

    plan = desktop_overlay.build_desktop_caption_plan(
        snapshot,
        window_width=1152,
        window_height=297,
        locale="zh-CN",
    )

    assert [line.text for line in plan.lines] == [
        "今日は PuriPuly Heart 좋아요 😊",
        "今日は mixed 원문 😊",
    ]
    assert plan.primary_font_size == 37
    assert plan.secondary_font_size == 23
    assert {line.font_family for line in plan.lines} == {"ResourceHanRoundedCN"}


class RecordingLifecycleSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def emit(self, event: dict[str, object]) -> None:
        self.events.append(dict(event))


class RecordingWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    async def send(self, payload: str) -> None:
        decoded = json.loads(payload)
        assert isinstance(decoded, dict)
        self.sent_messages.append(decoded)


class FakeFletWindow:
    def __init__(self, app: FakeFletApp) -> None:
        self._app = app
        self.visible: bool = False
        self.frameless: bool | None = None
        self.always_on_top: bool | None = None
        self.shadow: bool | None = None
        self.skip_task_bar: bool | None = None
        self.resizable: bool | None = None
        self.title_bar_hidden: bool | None = None
        self.title_bar_buttons_hidden: bool | None = None
        self.maximizable: bool | None = None
        self.bgcolor: object | None = None
        self.ignore_mouse_events: bool | None = None
        self.left: int | float = 0
        self.top: int | float = 0
        self.width: int | float = 0
        self.height: int | float = 0
        self.on_event: Any | None = None
        self.close_calls = 0
        self.destroy_calls = 0
        self.start_resizing_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self._app.closed.set()

    def destroy(self) -> None:
        self.destroy_calls += 1
        self._app.closed.set()

    def start_resizing(self, *_args: object, **_kwargs: object) -> None:
        self.start_resizing_calls += 1
        raise AssertionError("Flet 0.28.3 start_resizing must not be used")


class FakeFletPage:
    def __init__(self, app: FakeFletApp) -> None:
        self.window = FakeFletWindow(app)
        self.controls: list[object] = []
        self.bgcolor: object | None = None
        self.padding: object | None = None
        self.spacing: object | None = None
        self.horizontal_alignment: object | None = None
        self.vertical_alignment: object | None = None
        self.update_calls = 0
        self.render_snapshots: list[dict[str, object]] = []
        self.visibility_updates: list[bool] = []
        self.run_task_calls = 0
        self.tasks: list[asyncio.Task[object]] = []

    def add(self, *controls: object) -> None:
        self.controls.extend(controls)

    def clean(self) -> None:
        self.controls.clear()

    def update(self) -> None:
        self.update_calls += 1
        self.visibility_updates.append(self.window.visible)
        self.render_snapshots.append(
            {
                "ignore_mouse_events": self.window.ignore_mouse_events,
                "texts": _page_text_values(self),
                "has_drag_area": _page_contains_control_type(self, ft.WindowDragArea),
                "card_count": len(_caption_card_controls(self)),
            }
        )

    def run_task(self, func: Any, *args: object, **kwargs: object) -> asyncio.Task[object]:
        self.run_task_calls += 1
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            task = asyncio.create_task(result)  # type: ignore[arg-type]
        else:

            async def _completed() -> object:
                return result

            task = asyncio.create_task(_completed())
        self.tasks.append(task)
        return task


class FakeFletApp:
    def __init__(self) -> None:
        self.closed = asyncio.Event()
        self.page = FakeFletPage(self)
        self.targets: list[Any] = []

    async def run(self, target: Any) -> None:
        self.targets.append(target)
        result = target(self.page)
        if inspect.isawaitable(result):
            await result
        await self.closed.wait()


class FakeWindowEvent:
    def __init__(self, event_type: object) -> None:
        self.type = event_type
        self.data = event_type


def _walk_control_tree(control: object) -> list[object]:
    seen: list[object] = [control]
    for attr in ("content", "leading", "trailing"):
        child = getattr(control, attr, None)
        if child is not None:
            seen.extend(_walk_control_tree(child))
    children = getattr(control, "controls", None)
    if isinstance(children, list | tuple):
        for child in children:
            seen.extend(_walk_control_tree(child))
    return seen


def _page_text_values(page: FakeFletPage) -> set[str]:
    values: set[str] = set()
    for control in page.controls:
        for item in _walk_control_tree(control):
            value = getattr(item, "value", None)
            if isinstance(value, str):
                values.add(value)
            text = getattr(item, "text", None)
            if isinstance(text, str):
                values.add(text)
    return values


def _find_control_with_text(page: FakeFletPage, text: str) -> object:
    for control in page.controls:
        for item in _walk_control_tree(control):
            if getattr(item, "text", None) == text or getattr(item, "value", None) == text:
                return item
    raise AssertionError(f"control text not found: {text}")


def _page_contains_control_type(page: FakeFletPage, control_type: type[object]) -> bool:
    return any(
        isinstance(item, control_type)
        for control in page.controls
        for item in _walk_control_tree(control)
    )


OLD_OVERLAY_LOCAL_RENDERER_TEXT = {
    "Move captions",
    "Lock captions",
    "Reset to bottom center",
    "Drag edges to resize",
    "You can move this again from the main window.",
    "Captions will appear here",
    "Outline width",
    "Text scale",
}


def _caption_card_controls(page: FakeFletPage) -> list[ft.Container]:
    cards: list[ft.Container] = []
    for control in page.controls:
        for item in _walk_control_tree(control):
            if (
                isinstance(item, ft.Container)
                and getattr(item, "bgcolor", None) == "#80000000"
                and getattr(item, "border_radius", None) in {14, 16, 18, 20}
            ):
                cards.append(item)
    return cards


def _assert_no_overlay_local_renderer_text(page: FakeFletPage) -> None:
    assert _page_text_values(page).isdisjoint(OLD_OVERLAY_LOCAL_RENDERER_TEXT)


def test_desktop_overlay_preview_fixtures_cover_required_local_qa_cases() -> None:
    catalog = desktop_overlay.build_desktop_overlay_preview_catalog(locale="en")

    assert tuple(preset.id for preset in catalog.size_presets) == (
        "small",
        "medium",
        "large",
        "xlarge",
    )
    assert tuple(catalog.background_alpha_presets) == (0.35, 0.5, 0.65, 0.8)
    assert tuple(surface.id for surface in catalog.background_surfaces) == (
        "bright",
        "dark",
        "busy",
    )

    required_tags = {
        "ko",
        "ja",
        "zh-CN",
        "en",
        "mixed_script",
        "emoji",
        "self",
        "peer",
        "primary",
        "secondary",
        "active",
        "finalized",
        "long_wrap",
        "no_caption",
    }
    coverage_tags = {tag for fixture in catalog.fixtures for tag in fixture.coverage_tags}
    assert required_tags <= coverage_tags
    assert len({fixture.id for fixture in catalog.fixtures}) == len(catalog.fixtures)

    long_wrap_fixture = next(
        fixture for fixture in catalog.fixtures if "long_wrap" in fixture.coverage_tags
    )
    long_wrap_texts = [
        text
        for block in long_wrap_fixture.snapshot.blocks
        for text in (block.primary_text, block.secondary_text)
    ]
    assert any(len(text) >= 90 for text in long_wrap_texts)

    for fixture in catalog.fixtures:
        if "no_caption" in fixture.coverage_tags:
            assert fixture.snapshot.blocks == []
            continue
        assert fixture.snapshot.blocks, fixture.id
        plan = desktop_overlay.build_desktop_caption_plan(
            fixture.snapshot,
            window_width=1344,
            window_height=347,
            visual_state=desktop_overlay.DesktopCaptionVisualState(
                text_scale=1.0,
                background_alpha=0.5,
                outline_width=None,
            ),
        )
        assert plan.lines, fixture.id


def test_desktop_overlay_preview_no_caption_fixture_supports_manual_qa_states() -> None:
    catalog = desktop_overlay.build_desktop_overlay_preview_catalog(locale="en")

    fixture = next(fixture for fixture in catalog.fixtures if "no_caption" in fixture.coverage_tags)

    assert fixture.label == "No captions"
    edit_plan = desktop_overlay.build_desktop_caption_plan(
        fixture.snapshot,
        interaction_mode="edit",
        locale="en",
    )
    locked_plan = desktop_overlay.build_desktop_caption_plan(
        fixture.snapshot,
        interaction_mode="pass_through",
        locale="en",
    )
    assert edit_plan.lines == ()
    assert edit_plan.surface_visible is True
    assert edit_plan.background_alpha == pytest.approx(0.5)
    assert locked_plan.lines == ()
    assert locked_plan.surface_visible is False


def test_desktop_overlay_preview_fixture_data_secret_guard_rejects_bearer_tokens() -> None:
    catalog = desktop_overlay.build_desktop_overlay_preview_catalog(locale="en")

    assert desktop_overlay.preview_fixture_secret_findings(catalog) == ()

    malicious_fixture = replace(
        catalog.fixtures[0],
        snapshot=OverlayPresentationSnapshot(
            revision=999,
            blocks=[
                _block(
                    "preview-malicious-token",
                    channel="self",
                    block_variant="active_self",
                    appearance_seq=1,
                    primary_text="Authorization: Bearer real-preview-token-material",
                    secondary_text="sk-live-not-a-fixture-token-material",
                    secondary_enabled=True,
                )
            ],
        ),
    )
    unsafe_catalog = replace(catalog, fixtures=(malicious_fixture,))

    findings = desktop_overlay.preview_fixture_secret_findings(unsafe_catalog)

    assert len(findings) == 2
    assert all("korean_long_wrap" in finding for finding in findings)
    assert all("real-preview-token-material" not in finding for finding in findings)
    assert all("sk-live-not-a-fixture-token-material" not in finding for finding in findings)

    malicious_id_fixture = replace(
        catalog.fixtures[0],
        id="sk-live-fixture-id-token-material",
    )

    id_findings = desktop_overlay.preview_fixture_secret_findings(
        replace(catalog, fixtures=(malicious_id_fixture,))
    )

    assert id_findings
    assert all("sk-live-fixture-id-token-material" not in finding for finding in id_findings)


def test_desktop_overlay_preview_fixture_data_secret_guard_recurses_snapshot_metadata() -> None:
    catalog = desktop_overlay.build_desktop_overlay_preview_catalog(locale="en")
    fixture = replace(
        catalog.fixtures[0],
        snapshot=OverlayPresentationSnapshot(
            revision=1000,
            calibration=OverlayPresentationCalibration(
                anchor="Bearer calibration-anchor-token-material",
            ),
            blocks=[
                OverlayPresentationBlock(
                    id="preview-safe-metadata",
                    occupant_key="self:preview-safe-metadata",
                    appearance_seq=1,
                    channel="self",
                    block_variant="active_self",
                    primary_text="Safe preview text",
                    secondary_text="Safe secondary text",
                    secondary_enabled=True,
                    update_id="sk-live-update-id-token-material",
                    session_scope="Bearer session-scope-token-material",
                    source_text_hash="sk-live-source-hash-token-material",
                    logical_turn_key="Bearer logical-turn-token-material",
                )
            ],
        ),
    )

    findings = desktop_overlay.preview_fixture_secret_findings(
        replace(catalog, fixtures=(fixture,))
    )

    assert len(findings) == 5
    assert any("snapshot.calibration.anchor" in finding for finding in findings)
    assert any("snapshot.blocks[0].update_id" in finding for finding in findings)
    assert any("snapshot.blocks[0].session_scope" in finding for finding in findings)
    assert any("snapshot.blocks[0].source_text_hash" in finding for finding in findings)
    assert any("snapshot.blocks[0].logical_turn_key" in finding for finding in findings)
    assert all("token-material" not in finding for finding in findings)


def test_desktop_overlay_preview_fixture_data_secret_guard_scans_catalog_controls() -> None:
    catalog = desktop_overlay.build_desktop_overlay_preview_catalog(locale="en")
    unsafe_catalog = replace(
        catalog,
        labels=replace(
            catalog.labels,
            fixture="Bearer catalog-label-token-material",
        ),
        size_presets=(
            replace(
                catalog.size_presets[0],
                label="sk-live-size-preset-token-material",
            ),
            *catalog.size_presets[1:],
        ),
        background_surfaces=(
            replace(
                catalog.background_surfaces[0],
                bgcolor="Bearer surface-bg-token-material",
            ),
            *catalog.background_surfaces[1:],
        ),
    )

    findings = desktop_overlay.preview_fixture_secret_findings(unsafe_catalog)

    assert len(findings) == 3
    assert any("labels.fixture" in finding for finding in findings)
    assert any("size_presets[0].label" in finding for finding in findings)
    assert any("background_surfaces[0].bgcolor" in finding for finding in findings)
    assert all("token-material" not in finding for finding in findings)


def test_desktop_overlay_lifecycle_redaction_covers_common_secret_key_variants() -> None:
    redacted = desktop_overlay._redact_event(
        {
            "type": "runtime_error",
            "payload": {
                "api_key": "plain-api-key-value",
                "access_token": "plain-access-token-value",
                "sessionToken": "camel-session-token-value",
                "authorization_header": "plain-auth-header-value",
                "safe": "visible",
            },
        }
    )

    assert redacted == {
        "type": "runtime_error",
        "payload": {
            "api_key": "<redacted>",
            "access_token": "<redacted>",
            "sessionToken": "<redacted>",
            "authorization_header": "<redacted>",
            "safe": "visible",
        },
    }


def test_desktop_overlay_preview_fixture_data_packaging_readiness_is_embedded() -> None:
    sources = desktop_overlay.desktop_overlay_preview_fixture_data_sources()

    assert sources == (
        desktop_overlay.DesktopOverlayPreviewFixtureDataSource(
            source_kind="embedded_python_module",
            module="puripuly_heart.ui.desktop_overlay",
            package_data_globs=(),
            hiddenimports=(),
        ),
    )
    assert all(not source.package_data_globs for source in sources)
    assert all(not source.hiddenimports for source in sources)


def test_desktop_overlay_preview_guard_stops_before_rendering_unsafe_fixture_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = desktop_overlay.build_desktop_overlay_preview_catalog(locale="en")
    malicious_fixture = replace(
        catalog.fixtures[0],
        snapshot=OverlayPresentationSnapshot(
            revision=999,
            blocks=[
                _block(
                    "preview-malicious-token",
                    channel="self",
                    block_variant="active_self",
                    appearance_seq=1,
                    primary_text="Authorization: Bearer real-preview-token-material",
                )
            ],
        ),
    )
    unsafe_catalog = replace(catalog, fixtures=(malicious_fixture,))
    monkeypatch.setattr(
        desktop_overlay,
        "build_desktop_overlay_preview_catalog",
        lambda *, locale=None: unsafe_catalog,
    )

    def fail_app_runner(_target: Any) -> object:
        raise AssertionError("unsafe preview fixture data must not be rendered")

    assert desktop_overlay.run_preview(app_runner=fail_app_runner, locale="en") == 1


def test_desktop_overlay_preview_guard_avoids_provider_broker_stt_translation_secretstore_and_settings_save_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from puripuly_heart.config import settings as settings_module

    forbidden_prefixes = (
        "puripuly_heart.app.wiring",
        "puripuly_heart.app.headless_mic",
        "puripuly_heart.core.managed_openrouter_broker_client",
        "puripuly_heart.core.storage.secrets",
        "puripuly_heart.core.stt",
        "puripuly_heart.providers",
        "puripuly_heart.ui.controller",
    )
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if level == 0 and any(
            name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes
        ):
            raise AssertionError(f"preview must not import {name}")
        return original_import(name, globals_, locals_, fromlist, level)

    def fail_settings_save(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("preview must not call settings-save paths")

    def fail_write_text(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("preview must not persist fixture or settings data")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(settings_module, "save_settings", fail_settings_save)
    monkeypatch.setattr(Path, "write_text", fail_write_text)

    app = FakeFletApp()

    def run_preview_target(target: Any) -> None:
        target(app.page)

    assert desktop_overlay.run_preview(app_runner=run_preview_target, locale="en") == 0
    assert app.page.controls


def test_desktop_overlay_preview_fixtures_use_real_overlay_window_surface_and_edit_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_renderer_path(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("preview must not load a renderer manifest or start bridge runtime")

    monkeypatch.setattr(desktop_overlay, "load_renderer_manifest", fail_renderer_path)
    monkeypatch.setattr(desktop_overlay, "DesktopOverlayRenderer", fail_renderer_path)

    app = FakeFletApp()

    def run_preview_target(target: Any) -> None:
        app.targets.append(target)
        result = target(app.page)
        assert not inspect.isawaitable(result)

    assert desktop_overlay.run_preview(app_runner=run_preview_target, locale="en") == 0

    assert app.page.window.frameless is True
    assert app.page.window.always_on_top is True
    assert app.page.window.shadow is False
    assert app.page.window.skip_task_bar is True
    assert app.page.window.resizable is False
    assert app.page.window.bgcolor == ft.Colors.TRANSPARENT
    assert app.page.bgcolor == ft.Colors.TRANSPARENT
    assert app.page.window.ignore_mouse_events is False
    assert app.page.window.width >= desktop_overlay._DESKTOP_PREVIEW_STAGE_WIDTH
    assert app.page.window.height >= desktop_overlay._DESKTOP_PREVIEW_STAGE_HEIGHT
    assert not _page_contains_control_type(app.page, ft.WindowDragArea)

    visible_text = _page_text_values(app.page)
    assert "Sample captions" in visible_text
    assert "Desktop caption size" in visible_text
    assert "Preview background" in visible_text
    assert "Outline width" not in visible_text
    assert "Text scale" not in visible_text


@pytest.mark.asyncio
async def test_desktop_overlay_preview_controls_apply_size_preset_without_outline_controls() -> (
    None
):
    app = FakeFletApp()
    sink = RecordingLifecycleSink()
    catalog = desktop_overlay.build_desktop_overlay_preview_catalog(locale="en")
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=sink.emit,
        locale="en",
        bounds_debounce_s=0.01,
        preview_catalog=catalog,
    )

    try:
        await window.start(catalog.fixtures[0].snapshot)
        assert "Preview background" in _page_text_values(app.page)
        assert "Outline width" not in _page_text_values(app.page)

        large_control = _find_control_with_text(app.page, "Large")
        large_handler = getattr(large_control, "on_click", None)
        assert callable(large_handler)
        large_handler(None)
        if app.page.tasks:
            await asyncio.gather(*app.page.tasks)

        assert app.page.window.ignore_mouse_events is False
        assert app.page.window.width == 1600
        assert app.page.window.height == 413
        visible_text = _page_text_values(app.page)
        assert "Preview background" in visible_text
        assert sink.events == []
    finally:
        await window.close()


def test_desktop_overlay_preview_i18n_labels_resolve_for_all_controls() -> None:
    catalog = desktop_overlay.build_desktop_overlay_preview_catalog(locale="ja")

    assert catalog.labels.fixture == "サンプル字幕"
    assert catalog.labels.size_preset == "デスクトップ字幕のサイズ"
    assert catalog.labels.background_alpha == "背景の濃さ"
    assert catalog.labels.background_surface == "プレビュー背景"
    assert [preset.label for preset in catalog.size_presets] == [
        "小さめ",
        "標準",
        "大きめ",
        "さらに大きく",
    ]
    assert [surface.label for surface in catalog.background_surfaces] == [
        "明るい背景",
        "暗い背景",
        "にぎやかなデスクトップ",
    ]
    for fixture in catalog.fixtures:
        assert fixture.label.strip()
        assert not fixture.label.startswith("settings.")


def test_desktop_overlay_preview_fixtures_run_local_app_without_renderer_or_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_renderer_path(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("preview must not load a renderer manifest or start bridge runtime")

    def fail_write_text(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("preview must not persist settings or write local fixture data")

    monkeypatch.setattr(desktop_overlay, "load_renderer_manifest", fail_renderer_path)
    monkeypatch.setattr(desktop_overlay, "DesktopOverlayRenderer", fail_renderer_path)
    monkeypatch.setattr(Path, "write_text", fail_write_text)

    app = FakeFletApp()

    def run_preview_target(target: Any) -> None:
        app.targets.append(target)
        result = target(app.page)
        assert not inspect.isawaitable(result)

    assert desktop_overlay.run_preview(app_runner=run_preview_target, locale="en") == 0
    assert len(app.targets) == 1

    visible_text = _page_text_values(app.page)
    assert "Sample captions" in visible_text
    assert "Desktop caption size" in visible_text
    assert "Background opacity" in visible_text
    assert "Preview background" in visible_text
    assert "Outline width" not in visible_text
    assert "Text scale" not in visible_text
    assert {"0.35", "0.50", "0.65", "0.80"} <= visible_text
    assert {"Small", "Medium", "Large", "Extra large"} <= visible_text
    assert {"Bright", "Dark", "Busy desktop"} <= visible_text
    assert "Korean long wrap" in visible_text
    assert any("긴 문장" in text for text in visible_text)


@pytest.mark.asyncio
async def test_default_flet_app_runner_starts_hidden_to_prevent_startup_flash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_app_async(**kwargs: object) -> None:
        calls.append(dict(kwargs))

    def target(_page: object) -> None:
        return None

    monkeypatch.setattr(ft, "app_async", fake_app_async)

    await desktop_overlay._default_flet_app_runner(target)  # noqa: SLF001 - verify runner policy

    assert calls == [{"target": target, "view": ft.AppView.FLET_APP_HIDDEN}]


@pytest.mark.asyncio
async def test_hidden_flet_view_launcher_uses_windows_startup_hide_before_flet_env_hide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import flet_desktop

    created: list[dict[str, object]] = []
    fake_process = object()

    def fake_locate_and_unpack(page_url: str, assets_dir: str, hidden: bool):
        assert page_url == "flet://desktop-overlay"
        assert assets_dir == "assets"
        assert hidden is True
        return (
            ["C:/fake/flet.exe", page_url, "pid-file", assets_dir],
            {"FLET_HIDE_WINDOW_ON_START": "true"},
            "pid-file",
        )

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> object:
        created.append({"args": args, "kwargs": kwargs})
        return fake_process

    monkeypatch.setattr(
        flet_desktop,
        "__locate_and_unpack_flet_view",
        fake_locate_and_unpack,
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    process, pid_file = (
        await desktop_overlay._open_flet_view_hidden_without_startup_flash(  # noqa: SLF001 - verify launch boundary
            "flet://desktop-overlay",
            "assets",
            True,
        )
    )

    assert process is fake_process
    assert pid_file == "pid-file"
    assert created[0]["args"] == (
        "C:/fake/flet.exe",
        "flet://desktop-overlay",
        "pid-file",
        "assets",
    )
    kwargs = created[0]["kwargs"]
    assert kwargs["env"] == {"FLET_HIDE_WINDOW_ON_START": "true"}
    if sys.platform == "win32":
        startupinfo = kwargs["startupinfo"]
        assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert startupinfo.wShowWindow == subprocess.SW_HIDE
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW


@pytest.mark.asyncio
async def test_desktop_overlay_reveals_first_window_update_after_chrome_bounds_and_content() -> (
    None
):
    app = FakeFletApp()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=RecordingLifecycleSink().emit,
        locale="en",
        bounds_debounce_s=0.01,
    )
    window.prime_startup_runtime_controls(
        (
            {
                "command": "apply_window_bounds",
                "x": 320,
                "y": 720,
                "width": 1344,
                "height": 347,
            },
            {"command": "set_interaction_mode", "mode": "pass_through"},
        )
    )

    try:
        await window.start(OverlayPresentationSnapshot(revision=1, blocks=[]))

        assert app.page.window.visible is True
        assert app.page.visibility_updates == [True]
        assert app.page.window.frameless is True
        assert app.page.window.shadow is False
        assert app.page.window.resizable is False
        assert app.page.window.always_on_top is True
        assert (app.page.window.left, app.page.window.top) == (320, 720)
        assert (app.page.window.width, app.page.window.height) == (1344, 347)
        assert app.page.render_snapshots[0] == {
            "ignore_mouse_events": True,
            "texts": set(),
            "has_drag_area": False,
            "card_count": 0,
        }
    finally:
        await window.close()


@pytest.mark.asyncio
async def test_desktop_overlay_flet_window_starts_frameless_transparent_moving_empty_card() -> None:
    app = FakeFletApp()
    sink = RecordingLifecycleSink()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=sink.emit,
        locale="en",
        bounds_debounce_s=0.01,
    )

    try:
        await window.start(OverlayPresentationSnapshot(revision=1, blocks=[]))

        page = app.page
        assert page.window.frameless is True
        assert page.window.always_on_top is True
        assert page.window.shadow is False
        assert page.window.skip_task_bar is True
        assert page.window.resizable is False
        assert page.window.maximizable is False
        assert page.window.bgcolor == ft.Colors.TRANSPARENT
        assert page.bgcolor == ft.Colors.TRANSPARENT
        assert page.window.ignore_mouse_events is False
        assert page.window.title_bar_hidden is None
        assert page.window.title_bar_buttons_hidden is None
        assert page.window.on_event is not None
        assert page.window.start_resizing_calls == 0
        assert page.window.width == 1344
        assert page.window.height == 347

        assert _page_text_values(page) == set()
        _assert_no_overlay_local_renderer_text(page)
        assert _page_contains_control_type(page, ft.WindowDragArea)
        cards = _caption_card_controls(page)
        assert len(cards) == 1
        assert cards[0].bgcolor == "#80000000"
    finally:
        await window.close()


@pytest.mark.asyncio
async def test_desktop_overlay_display_matrix_moving_and_locked_with_captions() -> None:
    app = FakeFletApp()
    sink = RecordingLifecycleSink()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=sink.emit,
        locale="en",
        bounds_debounce_s=0.01,
    )
    captions = OverlayPresentationSnapshot(
        revision=2,
        blocks=[
            _block(
                "peer-translated",
                channel="peer",
                block_variant="finalized",
                appearance_seq=1,
                primary_text="좋아요",
                secondary_text="Sounds good",
                secondary_enabled=True,
            )
        ],
    )

    try:
        await window.start(captions)

        assert app.page.window.ignore_mouse_events is False
        assert _page_contains_control_type(app.page, ft.WindowDragArea)
        assert {"좋아요", "Sounds good"} <= _page_text_values(app.page)
        assert len(_caption_card_controls(app.page)) == 1

        chrome_before_lock = (
            app.page.window.frameless,
            app.page.window.shadow,
            app.page.window.resizable,
            app.page.window.always_on_top,
            app.page.window.title_bar_hidden,
            app.page.window.title_bar_buttons_hidden,
        )

        await window.dispatch_runtime_control(
            {"command": "set_interaction_mode", "mode": "pass_through"}
        )

        assert app.page.window.ignore_mouse_events is True
        assert (
            app.page.window.frameless,
            app.page.window.shadow,
            app.page.window.resizable,
            app.page.window.always_on_top,
            app.page.window.title_bar_hidden,
            app.page.window.title_bar_buttons_hidden,
        ) == chrome_before_lock
        assert {"좋아요", "Sounds good"} <= _page_text_values(app.page)
        assert not _page_contains_control_type(app.page, ft.WindowDragArea)
        assert len(_caption_card_controls(app.page)) == 1
        _assert_no_overlay_local_renderer_text(app.page)
        assert sink.events[-1] == {
            "type": "overlay_event",
            "payload": {"event": "interaction_mode_changed", "mode": "pass_through"},
        }
    finally:
        await window.close()


@pytest.mark.asyncio
async def test_desktop_overlay_display_matrix_locked_no_captions_is_fully_transparent() -> None:
    app = FakeFletApp()
    sink = RecordingLifecycleSink()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=sink.emit,
        locale="en",
        bounds_debounce_s=0.01,
    )

    try:
        await window.start(OverlayPresentationSnapshot(revision=1, blocks=[]))

        assert app.page.window.frameless is True
        assert app.page.window.shadow is False
        assert app.page.window.resizable is False
        assert app.page.window.always_on_top is True
        assert app.page.window.ignore_mouse_events is False
        assert _page_contains_control_type(app.page, ft.WindowDragArea)
        assert len(_caption_card_controls(app.page)) == 1

        await window.dispatch_runtime_control(
            {"command": "set_interaction_mode", "mode": "pass_through"}
        )

        assert app.page.window.title_bar_hidden is None
        assert app.page.window.title_bar_buttons_hidden is None
        assert app.page.window.resizable is False
        assert app.page.window.frameless is True
        assert app.page.window.shadow is False
        assert app.page.window.always_on_top is True
        assert app.page.window.ignore_mouse_events is True
        assert _page_text_values(app.page) == set()
        assert _caption_card_controls(app.page) == []
        assert not _page_contains_control_type(app.page, ft.WindowDragArea)

        await window.dispatch_runtime_control({"command": "set_interaction_mode", "mode": "edit"})

        assert app.page.window.title_bar_hidden is None
        assert app.page.window.title_bar_buttons_hidden is None
        assert app.page.window.resizable is False
        assert app.page.window.ignore_mouse_events is False
        assert _page_contains_control_type(app.page, ft.WindowDragArea)
        assert len(_caption_card_controls(app.page)) == 1
    finally:
        await window.close()


@pytest.mark.asyncio
async def test_desktop_overlay_preset_visual_tokens_match_product_table() -> None:
    expected = {
        "small": (1152, 297, 37, 23, 18, 10, 14),
        "medium": (1344, 347, 43, 27, 22, 12, 16),
        "large": (1600, 413, 52, 32, 26, 14, 18),
        "xlarge": (1792, 462, 58, 36, 30, 16, 20),
    }

    for preset_id, (
        width,
        height,
        primary,
        secondary,
        padding_h,
        padding_v,
        radius,
    ) in expected.items():
        plan = desktop_overlay.build_desktop_caption_plan(
            OverlayPresentationSnapshot(
                blocks=[
                    _block(
                        f"{preset_id}-caption",
                        channel="peer",
                        block_variant="finalized",
                        appearance_seq=1,
                        primary_text="caption",
                    )
                ]
            ),
            window_width=width,
            window_height=height,
            interaction_mode="edit",
        )

        assert plan.size_preset == preset_id
        assert plan.window_width == width
        assert plan.window_height == height
        assert plan.primary_font_size == primary
        assert plan.secondary_font_size == secondary
        assert plan.padding_horizontal == padding_h
        assert plan.padding_vertical == padding_v
        assert plan.border_radius == radius

        surface = desktop_overlay.build_desktop_caption_surface(plan)
        assert surface.padding.left == padding_h
        assert surface.padding.top == padding_v
        assert surface.border_radius == radius


@pytest.mark.asyncio
async def test_desktop_overlay_shipping_surface_has_no_overlay_local_controls() -> None:
    app = FakeFletApp()
    sink = RecordingLifecycleSink()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=sink.emit,
        locale="en",
        bounds_debounce_s=0.01,
    )

    try:
        await window.start(OverlayPresentationSnapshot(revision=1, blocks=[]))
        assert app.page.run_task_calls == 0
        assert sink.events == []
        _assert_no_overlay_local_renderer_text(app.page)
        assert not any(
            isinstance(item, (ft.ElevatedButton, ft.TextButton))
            for control in app.page.controls
            for item in _walk_control_tree(control)
        )
    finally:
        await window.close()


@pytest.mark.asyncio
async def test_desktop_overlay_interaction_mode_bounds_and_visual_runtime_controls_validate_atomically() -> (
    None
):
    app = FakeFletApp()
    sink = RecordingLifecycleSink()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=sink.emit,
        locale="en",
        bounds_debounce_s=0.01,
    )

    try:
        await window.start(OverlayPresentationSnapshot(revision=1, blocks=[]))
        await window.dispatch_runtime_control(
            {
                "command": "apply_window_bounds",
                "x": 320,
                "y": 720,
                "width": 1280,
                "height": 330,
            }
        )
        await window.dispatch_runtime_control(
            {
                "command": "apply_visual_config",
                "text_scale": 1.25,
                "background_alpha": 0.65,
                "outline_width": 2.5,
            }
        )
        await window.dispatch_runtime_control(
            {"command": "set_interaction_mode", "mode": "pass_through"}
        )
        state_after_valid_controls = (
            app.page.window.left,
            app.page.window.top,
            app.page.window.width,
            app.page.window.height,
            app.page.window.ignore_mouse_events,
            _page_text_values(app.page),
        )

        await window.dispatch_runtime_control({"command": "set_interaction_mode", "mode": "bogus"})
        await window.dispatch_runtime_control(
            {"command": "apply_window_bounds", "x": 1, "y": 2, "width": 0, "height": 0}
        )
        await window.dispatch_runtime_control(
            {
                "command": "apply_visual_config",
                "text_scale": True,
                "background_alpha": 2.0,
                "outline_width": -1.0,
            }
        )
        await window.dispatch_runtime_control({"command": "unknown_desktop_command"})

        assert (
            app.page.window.left,
            app.page.window.top,
            app.page.window.width,
            app.page.window.height,
            app.page.window.ignore_mouse_events,
            _page_text_values(app.page),
        ) == state_after_valid_controls
        assert sink.events[-1] == {
            "type": "overlay_event",
            "payload": {"event": "interaction_mode_changed", "mode": "pass_through"},
        }
    finally:
        await window.close()


@pytest.mark.asyncio
async def test_desktop_overlay_window_bounds_events_debounce_zero_samples_and_programmatic_echoes() -> (
    None
):
    app = FakeFletApp()
    sink = RecordingLifecycleSink()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=sink.emit,
        locale="en",
        bounds_debounce_s=0.01,
    )

    try:
        await window.start(OverlayPresentationSnapshot(revision=1, blocks=[]))
        assert callable(app.page.window.on_event)

        app.page.window.left = 0
        app.page.window.top = 0
        app.page.window.width = 0
        app.page.window.height = 0
        app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.MOVE))
        await asyncio.sleep(0.03)
        assert sink.events == []

        app.page.window.left = 100
        app.page.window.top = 200
        app.page.window.width = 900
        app.page.window.height = 240
        app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.MOVE))
        app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.MOVED))
        await asyncio.sleep(0.03)
        assert sink.events == [
            {
                "type": "overlay_event",
                "payload": {
                    "event": "window_bounds_changed",
                    "source": "user",
                    "persist": True,
                    "bounds_epoch": 0,
                    "x": 100,
                    "y": 200,
                    "width": 900,
                    "height": 240,
                },
            }
        ]

        await window.dispatch_runtime_control(
            {
                "command": "apply_window_bounds",
                "x": 320,
                "y": 720,
                "width": 1280,
                "height": 330,
            }
        )
        app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.RESIZED))
        await asyncio.sleep(0.03)
        assert len(sink.events) == 1

        app.page.window.left = 360
        app.page.window.top = 700
        app.page.window.width = 1280
        app.page.window.height = 330
        app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.RESIZE))
        await asyncio.sleep(0.03)
        assert sink.events[-1]["payload"] == {
            "event": "window_bounds_changed",
            "source": "user",
            "persist": True,
            "bounds_epoch": 0,
            "x": 360,
            "y": 700,
            "width": 1280,
            "height": 330,
        }
    finally:
        await window.close()


@pytest.mark.asyncio
async def test_desktop_overlay_shutdown_cancels_queued_bounds_callback_without_event() -> None:
    app = FakeFletApp()
    sink = RecordingLifecycleSink()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=sink.emit,
        locale="en",
        bounds_debounce_s=0.01,
    )

    await window.start(OverlayPresentationSnapshot(revision=1, blocks=[]))
    assert callable(app.page.window.on_event)
    app.page.window.left = 120
    app.page.window.top = 240
    app.page.window.width = 900
    app.page.window.height = 240

    app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.MOVED))
    await window.close()
    if app.page.tasks:
        await asyncio.gather(*app.page.tasks, return_exceptions=True)
    await asyncio.sleep(0.03)

    assert sink.events == []
    assert app.page.window.on_event is None
    bounds_task = window._bounds_sample_task  # noqa: SLF001 - assert shutdown cleanup
    assert bounds_task is None or bounds_task.done()


@pytest.mark.asyncio
async def test_desktop_overlay_bounds_programmatic_echo_suppression_is_bounded_and_tolerant() -> (
    None
):
    app = FakeFletApp()
    sink = RecordingLifecycleSink()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=sink.emit,
        locale="en",
        bounds_debounce_s=0.01,
    )

    try:
        await window.start(OverlayPresentationSnapshot(revision=1, blocks=[]))
        assert callable(app.page.window.on_event)
        await window.dispatch_runtime_control(
            {
                "command": "apply_window_bounds",
                "x": 320,
                "y": 720,
                "width": 1280,
                "height": 330,
            }
        )

        app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.RESIZED))
        await asyncio.sleep(0.03)
        assert sink.events == []

        app.page.window.left = 320.4
        app.page.window.top = 719.6
        app.page.window.width = 1279.8
        app.page.window.height = 330.2
        app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.MOVED))
        await asyncio.sleep(0.03)
        assert sink.events == []

        app.page.window.left = 321
        app.page.window.top = 720
        app.page.window.width = 1280
        app.page.window.height = 330
        app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.RESIZE))
        await asyncio.sleep(0.03)
        assert sink.events == []

        await asyncio.sleep(0.30)
        app.page.window.left = 360
        app.page.window.top = 700
        app.page.window.width = 1280
        app.page.window.height = 330
        app.page.window.on_event(FakeWindowEvent(ft.WindowEventType.MOVED))
        await asyncio.sleep(0.03)

        assert sink.events == [
            {
                "type": "overlay_event",
                "payload": {
                    "event": "window_bounds_changed",
                    "source": "user",
                    "persist": True,
                    "bounds_epoch": 0,
                    "x": 360,
                    "y": 700,
                    "width": 1280,
                    "height": 330,
                },
            }
        ]
    finally:
        await window.close()


class FakeRendererWindow:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.close_calls = 0
        self.snapshots: list[OverlayPresentationSnapshot] = []
        self.runtime_controls: list[dict[str, object]] = []

    async def start(self, initial_snapshot: OverlayPresentationSnapshot) -> None:
        self.snapshots.append(initial_snapshot)
        self.started.set()

    async def run_until_closed(self) -> None:
        await self.closed.wait()

    async def close(self) -> None:
        self.close_calls += 1
        self.closed.set()

    async def dispatch_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None:
        self.snapshots.append(snapshot)

    async def dispatch_runtime_control(self, payload: dict[str, object]) -> None:
        self.runtime_controls.append(dict(payload))


class FailingStartWindow(FakeRendererWindow):
    async def start(self, initial_snapshot: OverlayPresentationSnapshot) -> None:
        await super().start(initial_snapshot)
        raise RuntimeError("window bootstrap failed")


class FakeParentMonitor:
    def __init__(self) -> None:
        self.exited = asyncio.Event()
        self.started = asyncio.Event()

    async def wait_for_parent_exit(self, stop_event: asyncio.Event) -> None:
        self.started.set()
        exit_task = asyncio.create_task(self.exited.wait())
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {exit_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                return
        finally:
            for task in (exit_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(exit_task, stop_task, return_exceptions=True)


class ClosableFakeParentMonitor(FakeParentMonitor):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


async def _next_bridge_event(
    bridge: OverlayBridge,
    *,
    expected_type: str,
) -> dict[str, Any]:
    while True:
        event = await asyncio.wait_for(bridge.messages.get(), timeout=1.0)
        if event.get("type") == expected_type:
            return event


def test_desktop_overlay_parent_monitor_factory_prefers_windows_handle() -> None:
    monitor = desktop_overlay.create_parent_monitor(
        4321,
        is_windows=True,
        open_windows_handle=lambda pid: f"handle-{pid}",
    )

    assert isinstance(monitor, desktop_overlay.WindowsParentHandleMonitor)
    assert monitor.handle == "handle-4321"


def test_desktop_overlay_parent_monitor_factory_falls_back_when_handle_unavailable() -> None:
    monitor = desktop_overlay.create_parent_monitor(
        4321,
        is_windows=True,
        open_windows_handle=lambda _pid: None,
    )

    assert isinstance(monitor, desktop_overlay.BridgeDisconnectParentMonitor)
    assert monitor.parent_pid == 4321


@pytest.mark.asyncio
async def test_desktop_overlay_windows_handle_fallback_does_not_probe_with_os_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_os_kill(_pid: int, _signal: int) -> None:
        raise AssertionError("Windows parent monitor fallback must not call os.kill(pid, 0)")

    monkeypatch.setattr(desktop_overlay.os, "kill", fail_os_kill)
    monitor = desktop_overlay.create_parent_monitor(
        4321,
        is_windows=True,
        open_windows_handle=lambda _pid: None,
    )
    stop_event = asyncio.Event()

    wait_task = asyncio.create_task(monitor.wait_for_parent_exit(stop_event))
    await asyncio.sleep(0)
    stop_event.set()

    await asyncio.wait_for(wait_task, timeout=1.0)


def test_desktop_overlay_manifest_rejects_non_loopback_bridge_and_redacts_token(
    tmp_path: Path,
) -> None:
    token = "super-secret-session-token"
    manifest_path = _write_manifest(
        tmp_path,
        _manifest(bridge_url="wss://example.com:8765/overlay", session_token=token),
    )

    with pytest.raises(desktop_overlay.DesktopOverlayStartupError) as exc_info:
        desktop_overlay.load_renderer_manifest(manifest_path)

    error = exc_info.value
    assert error.failure_reason == "manifest_invalid"
    assert token not in str(error)
    assert token not in repr(error)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_token", None),
        ("session_token", ""),
        ("parent_pid", True),
        ("startup_deadline_ms", False),
    ],
)
def test_desktop_overlay_manifest_rejects_missing_or_bool_required_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload = _manifest().to_dict()
    payload[field] = value
    manifest_path = tmp_path / "overlay-manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(desktop_overlay.DesktopOverlayStartupError) as exc_info:
        desktop_overlay.load_renderer_manifest(manifest_path)

    assert exc_info.value.failure_reason == "manifest_invalid"


@pytest.mark.parametrize("url", ["ws://127.0.0.1:8765", "ws://[::1]:8765"])
def test_desktop_overlay_manifest_accepts_documented_loopback_bridge_urls(
    tmp_path: Path,
    url: str,
) -> None:
    manifest_path = _write_manifest(tmp_path, _manifest(bridge_url=url))

    manifest = desktop_overlay.load_renderer_manifest(manifest_path)

    assert manifest.bridge_url == url


@pytest.mark.asyncio
async def test_desktop_overlay_bridge_lifecycle_ready_after_auth_snapshot_and_window_start() -> (
    None
):
    token = "ready-session-token"
    bridge = OverlayBridge(
        session_token=token,
        initial_snapshot=OverlayPresentationSnapshot(revision=7),
        heartbeat_interval_ms=20,
        desktop_runtime_controls_enabled=True,
    )
    await bridge.start()
    sink = RecordingLifecycleSink()
    window = FakeRendererWindow()
    parent_monitor = FakeParentMonitor()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url=bridge.url, session_token=token),
        window=window,
        lifecycle_sink=sink,
        parent_monitor=parent_monitor,
    )

    try:
        run_task = asyncio.create_task(renderer.run())
        ready_event = await _next_bridge_event(bridge, expected_type="overlay_ready")

        assert ready_event == {"type": "overlay_ready"}
        assert window.started.is_set()
        assert window.snapshots[0].revision == 7
        assert sink.events[-1] == {"type": "overlay_ready"}
        assert token not in json.dumps(sink.events)

        await bridge.broadcast_shutdown()
        assert await asyncio.wait_for(run_task, timeout=1.0) == 0
    finally:
        await renderer.shutdown()
        await bridge.stop()


@pytest.mark.asyncio
async def test_desktop_overlay_malformed_initial_snapshot_is_startup_error_with_fallback() -> None:
    token = "malformed-initial-secret"
    received: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    async def handler(connection: Any) -> None:
        auth = json.loads(await connection.recv())
        assert auth == {"type": "auth", "session_token": token}
        await connection.send(
            json.dumps(
                {
                    "type": "snapshot",
                    "payload": {"revision": 1, "calibration": {}, "blocks": "not-a-list"},
                }
            )
        )
        message = json.loads(await asyncio.wait_for(connection.recv(), timeout=1.0))
        await received.put(message)

    server = await websockets.serve(handler, "127.0.0.1", 0, ping_interval=None)
    host, port = server.sockets[0].getsockname()[:2]
    sink = RecordingLifecycleSink()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url=f"ws://{host}:{port}", session_token=token),
        window=FakeRendererWindow(),
        lifecycle_sink=sink,
        parent_monitor=FakeParentMonitor(),
    )

    try:
        assert await renderer.run() == 1
        bridge_event = await asyncio.wait_for(received.get(), timeout=1.0)
    finally:
        await renderer.shutdown()
        server.close()
        await server.wait_closed()

    expected = {"type": "startup_error", "failure_reason": "renderer_init_failed"}
    assert bridge_event == expected
    assert sink.events[-1] == expected
    assert token not in json.dumps(sink.events)
    assert token not in json.dumps(bridge_event)


@pytest.mark.asyncio
async def test_desktop_overlay_window_start_failure_reports_window_configuration_error() -> None:
    token = "window-start-secret-token"
    bridge = OverlayBridge(
        session_token=token,
        initial_snapshot=OverlayPresentationSnapshot(revision=3),
        heartbeat_interval_ms=20,
        desktop_runtime_controls_enabled=True,
    )
    await bridge.start()
    sink = RecordingLifecycleSink()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url=bridge.url, session_token=token),
        window=FailingStartWindow(),
        lifecycle_sink=sink,
        parent_monitor=FakeParentMonitor(),
    )

    try:
        assert await renderer.run() == 1
        bridge_event = await _next_bridge_event(bridge, expected_type="startup_error")
    finally:
        await renderer.shutdown()
        await bridge.stop()

    expected = {"type": "startup_error", "failure_reason": "window_configuration_failed"}
    assert bridge_event == expected
    assert sink.events[-1] == expected
    assert token not in json.dumps(sink.events)
    assert token not in json.dumps(bridge_event)


@pytest.mark.asyncio
async def test_desktop_overlay_later_malformed_snapshot_is_ignored_and_controls_dispatch() -> None:
    token = "later-snapshot-token"
    bridge = OverlayBridge(
        session_token=token,
        initial_snapshot=OverlayPresentationSnapshot(revision=1),
        heartbeat_interval_ms=20,
        desktop_runtime_controls_enabled=True,
    )
    await bridge.start()
    window = FakeRendererWindow()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url=bridge.url, session_token=token),
        window=window,
        lifecycle_sink=RecordingLifecycleSink(),
        parent_monitor=FakeParentMonitor(),
    )

    try:
        run_task = asyncio.create_task(renderer.run())
        await _next_bridge_event(bridge, expected_type="overlay_ready")

        await bridge._broadcast_json(  # noqa: SLF001 - inject malformed renderer input
            {"type": "snapshot", "payload": {"revision": 2, "calibration": {}, "blocks": "bad"}}
        )
        await bridge.broadcast_desktop_runtime_control(
            {"command": "set_interaction_mode", "mode": "edit"}
        )
        await asyncio.sleep(0.05)

        assert [snapshot.revision for snapshot in window.snapshots] == [1]
        assert window.runtime_controls == [{"command": "set_interaction_mode", "mode": "edit"}]

        await bridge.broadcast_shutdown()
        assert await asyncio.wait_for(run_task, timeout=1.0) == 0
    finally:
        await renderer.shutdown()
        await bridge.stop()


@pytest.mark.asyncio
async def test_desktop_overlay_initial_interaction_mode_and_bounds_controls_apply_before_ready_event() -> (
    None
):
    token = "initial-control-token"
    initial_controls = [
        {
            "command": "apply_window_bounds",
            "x": 320,
            "y": 720,
            "width": 1600,
            "height": 413,
        },
        {"command": "set_interaction_mode", "mode": "pass_through"},
    ]
    bridge = OverlayBridge(
        session_token=token,
        initial_snapshot=OverlayPresentationSnapshot(revision=1),
        heartbeat_interval_ms=20,
        desktop_runtime_controls_enabled=True,
    )
    bridge.set_initial_desktop_runtime_controls(initial_controls)
    await bridge.start()
    window = FakeRendererWindow()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url=bridge.url, session_token=token),
        window=window,
        lifecycle_sink=RecordingLifecycleSink(),
        parent_monitor=FakeParentMonitor(),
    )

    try:
        run_task = asyncio.create_task(renderer.run())
        await _next_bridge_event(bridge, expected_type="overlay_ready")

        assert window.runtime_controls == initial_controls

        await bridge.broadcast_shutdown()
        assert await asyncio.wait_for(run_task, timeout=1.0) == 0
    finally:
        await renderer.shutdown()
        await bridge.stop()


@pytest.mark.asyncio
async def test_desktop_overlay_initial_locked_runtime_control_applies_before_first_flet_render() -> (
    None
):
    token = "initial-locked-real-window-token"
    bridge = OverlayBridge(
        session_token=token,
        initial_snapshot=OverlayPresentationSnapshot(revision=1, blocks=[]),
        heartbeat_interval_ms=20,
        desktop_runtime_controls_enabled=True,
    )
    bridge.set_initial_desktop_runtime_controls(
        [
            {
                "command": "apply_window_bounds",
                "x": 320,
                "y": 720,
                "width": 1344,
                "height": 347,
            },
            {
                "command": "apply_visual_config",
                "text_scale": 1.0,
                "background_alpha": 0.5,
                "outline_width": None,
            },
            {"command": "set_interaction_mode", "mode": "pass_through"},
        ]
    )
    await bridge.start()
    app = FakeFletApp()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=RecordingLifecycleSink().emit,
        locale="en",
        bounds_debounce_s=0.01,
    )
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url=bridge.url, session_token=token),
        window=window,
        lifecycle_sink=RecordingLifecycleSink(),
        parent_monitor=FakeParentMonitor(),
    )

    try:
        run_task = asyncio.create_task(renderer.run())
        await _next_bridge_event(bridge, expected_type="overlay_ready")

        assert app.page.render_snapshots
        first_render = app.page.render_snapshots[0]
        assert first_render == {
            "ignore_mouse_events": True,
            "texts": set(),
            "has_drag_area": False,
            "card_count": 0,
        }
        assert app.page.window.frameless is True
        assert app.page.window.shadow is False
        assert app.page.window.resizable is False
        assert app.page.window.always_on_top is True
        assert app.page.window.title_bar_hidden is None
        assert app.page.window.title_bar_buttons_hidden is None

        await bridge.broadcast_shutdown()
        assert await asyncio.wait_for(run_task, timeout=1.0) == 0
    finally:
        await renderer.shutdown()
        await bridge.stop()


@pytest.mark.asyncio
async def test_desktop_overlay_primed_initial_controls_are_not_replayed_after_start() -> None:
    token = "initial-control-replay-token"
    initial_controls = [
        {
            "command": "apply_window_bounds",
            "x": 320,
            "y": 720,
            "width": 1344,
            "height": 347,
        },
        {
            "command": "apply_visual_config",
            "text_scale": 1.0,
            "background_alpha": 0.5,
            "outline_width": None,
        },
        {"command": "set_interaction_mode", "mode": "pass_through"},
    ]
    bridge = OverlayBridge(
        session_token=token,
        initial_snapshot=OverlayPresentationSnapshot(revision=1, blocks=[]),
        heartbeat_interval_ms=20,
        desktop_runtime_controls_enabled=True,
    )
    bridge.set_initial_desktop_runtime_controls(initial_controls)
    await bridge.start()
    app = FakeFletApp()
    window = desktop_overlay.FletDesktopRendererWindow(
        app_runner=app.run,
        event_sink=RecordingLifecycleSink().emit,
        locale="en",
        bounds_debounce_s=0.01,
    )
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url=bridge.url, session_token=token),
        window=window,
        lifecycle_sink=RecordingLifecycleSink(),
        parent_monitor=FakeParentMonitor(),
    )

    try:
        run_task = asyncio.create_task(renderer.run())
        await _next_bridge_event(bridge, expected_type="overlay_ready")

        assert len(app.page.render_snapshots) == 1
        assert app.page.render_snapshots[0] == {
            "ignore_mouse_events": True,
            "texts": set(),
            "has_drag_area": False,
            "card_count": 0,
        }
        assert (app.page.window.left, app.page.window.top) == (320, 720)
        assert (app.page.window.width, app.page.window.height) == (1344, 347)

        await bridge.broadcast_shutdown()
        assert await asyncio.wait_for(run_task, timeout=1.0) == 0
    finally:
        await renderer.shutdown()
        await bridge.stop()


@pytest.mark.asyncio
async def test_desktop_overlay_events_are_bridge_only_and_do_not_use_stdout_fallback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    websocket = RecordingWebSocket()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(),
        window=FakeRendererWindow(),
        lifecycle_sink=desktop_overlay.StdoutLifecycleSink(),
        parent_monitor=FakeParentMonitor(),
    )
    renderer._websocket = websocket  # noqa: SLF001 - verify renderer channel routing
    overlay_event = {
        "type": "overlay_event",
        "payload": {"event": "interaction_mode_changed", "mode": "pass_through"},
    }

    try:
        await renderer._emit_lifecycle(overlay_event)  # noqa: SLF001 - verify routing
        captured = capsys.readouterr()

        assert websocket.sent_messages == [overlay_event]
        assert captured.out == ""
        assert captured.err == ""
    finally:
        await renderer.shutdown()


@pytest.mark.asyncio
async def test_desktop_overlay_lifecycle_errors_keep_stderr_fallback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    websocket = RecordingWebSocket()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(),
        window=FakeRendererWindow(),
        lifecycle_sink=desktop_overlay.StdoutLifecycleSink(),
        parent_monitor=FakeParentMonitor(),
    )
    renderer._websocket = websocket  # noqa: SLF001 - verify renderer channel routing
    runtime_error = {"type": "runtime_error", "failure_reason": "runtime_disconnected"}

    try:
        await renderer._emit_lifecycle(runtime_error)  # noqa: SLF001 - verify routing
        captured = capsys.readouterr()

        assert websocket.sent_messages == [runtime_error]
        assert captured.out == ""
        assert json.loads(captured.err) == runtime_error
    finally:
        await renderer.shutdown()


@pytest.mark.asyncio
async def test_desktop_overlay_invalid_runtime_control_reports_error_without_dispatch() -> None:
    token = "runtime-control-token"
    bridge = OverlayBridge(
        session_token=token,
        initial_snapshot=OverlayPresentationSnapshot(revision=1),
        heartbeat_interval_ms=20,
        desktop_runtime_controls_enabled=True,
    )
    await bridge.start()
    sink = RecordingLifecycleSink()
    window = FakeRendererWindow()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url=bridge.url, session_token=token),
        window=window,
        lifecycle_sink=sink,
        parent_monitor=FakeParentMonitor(),
    )

    try:
        run_task = asyncio.create_task(renderer.run())
        await _next_bridge_event(bridge, expected_type="overlay_ready")

        await bridge._broadcast_json(
            {"type": "runtime_control", "payload": ["bad"]}
        )  # noqa: SLF001
        runtime_error = await _next_bridge_event(bridge, expected_type="runtime_error")

        assert runtime_error == {
            "type": "runtime_error",
            "failure_reason": "runtime_control_invalid",
        }
        assert window.runtime_controls == []
        assert await asyncio.wait_for(run_task, timeout=1.0) == 1
    finally:
        await renderer.shutdown()
        await bridge.stop()


@pytest.mark.asyncio
async def test_desktop_overlay_parent_monitor_loss_reports_error_and_shutdown_is_idempotent() -> (
    None
):
    token = "parent-loss-token"
    bridge = OverlayBridge(
        session_token=token,
        initial_snapshot=OverlayPresentationSnapshot(revision=1),
        heartbeat_interval_ms=20,
        desktop_runtime_controls_enabled=True,
    )
    await bridge.start()
    window = FakeRendererWindow()
    parent_monitor = FakeParentMonitor()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url=bridge.url, session_token=token),
        window=window,
        lifecycle_sink=RecordingLifecycleSink(),
        parent_monitor=parent_monitor,
    )

    try:
        run_task = asyncio.create_task(renderer.run())
        await _next_bridge_event(bridge, expected_type="overlay_ready")
        await asyncio.wait_for(parent_monitor.started.wait(), timeout=1.0)

        parent_monitor.exited.set()
        runtime_error = await _next_bridge_event(bridge, expected_type="runtime_error")

        assert runtime_error == {"type": "runtime_error", "failure_reason": "runtime_disconnected"}
        assert await asyncio.wait_for(run_task, timeout=1.0) == 1

        await renderer.shutdown()
        await renderer.shutdown()
        assert window.close_calls == 1
        assert renderer.is_shutdown is True
    finally:
        await renderer.shutdown()
        await bridge.stop()


@pytest.mark.asyncio
async def test_desktop_overlay_startup_failure_closes_parent_monitor_once() -> None:
    parent_monitor = ClosableFakeParentMonitor()
    renderer = desktop_overlay.DesktopOverlayRenderer(
        _manifest(bridge_url="ws://192.0.2.10:8765"),
        window=FakeRendererWindow(),
        lifecycle_sink=RecordingLifecycleSink(),
        parent_monitor=parent_monitor,
    )

    assert await renderer.run() == 1
    await renderer.shutdown()

    assert parent_monitor.close_calls == 1
