from __future__ import annotations

# ruff: noqa: I001

import json
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.debug_preview_panel import (  # noqa: E402
    DEBUG_PREVIEW_PANEL_DATA_KEY,
    DebugPreviewPanel,
)
import puripuly_heart.ui.components.debug_preview_panel as panel_module  # noqa: E402


DEBUG_PREVIEW_I18N_KEYS = {
    "debug_preview.button",
    "debug_preview.tooltip",
    "debug_preview.brake_notice",
    "debug_preview.revoked_notice",
    "debug_preview.founder_letter",
    "debug_preview.pkce_failure",
    "debug_preview.discord_auth",
    "debug_preview.peer_translation_eula",
    "peer_translation_eula.title",
    "peer_translation_eula.body",
    "peer_translation_eula.accept",
    "peer_translation_eula.cancel",
    "peer_translation.disclosure",
}

ACTION_KEYS = [
    "brake_notice",
    "revoked_notice",
    "founder_letter",
    "pkce_failure",
    "discord_auth",
    "peer_translation_eula",
]


def _callbacks(seen: list[str]):
    return {
        "on_brake_notice": lambda: seen.append("brake_notice"),
        "on_revoked_notice": lambda: seen.append("revoked_notice"),
        "on_founder_letter": lambda: seen.append("founder_letter"),
        "on_pkce_failure": lambda: seen.append("pkce_failure"),
        "on_discord_auth": lambda: seen.append("discord_auth"),
        "on_peer_translation_eula": lambda: seen.append("peer_translation_eula"),
    }


def _button_label(button) -> str:
    if hasattr(button, "text"):
        return button.text
    return button.content


def test_debug_preview_panel_starts_collapsed_with_dbg_button() -> None:
    seen: list[str] = []

    panel = DebugPreviewPanel(**_callbacks(seen))

    assert panel.data == DEBUG_PREVIEW_PANEL_DATA_KEY
    assert _button_label(panel._toggle_button) == "DBG"
    assert panel._toggle_button.tooltip == "Debug UI preview"
    assert panel._popover.visible is False
    assert list(panel._action_buttons) == ACTION_KEYS
    assert seen == []


def test_debug_preview_panel_toggle_shows_and_hides_popover() -> None:
    panel = DebugPreviewPanel(**_callbacks([]))

    panel._toggle(None)
    assert panel._popover.visible is True

    panel._toggle(None)
    assert panel._popover.visible is False


def test_debug_preview_panel_skips_update_when_detached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = DebugPreviewPanel(**_callbacks([]))

    def fail_update(self) -> None:
        _ = self
        raise AssertionError("detached debug preview panel should not update")

    monkeypatch.setattr(DebugPreviewPanel, "update", fail_update)

    panel._toggle(None)
    panel.apply_locale()

    assert panel._popover.visible is True


def test_debug_preview_panel_invokes_each_callback_without_auto_collapsing() -> None:
    seen: list[str] = []
    panel = DebugPreviewPanel(**_callbacks(seen))
    panel._toggle(None)

    for action_key in ACTION_KEYS:
        panel._action_buttons[action_key].on_click(None)

    assert seen == ACTION_KEYS
    assert panel._popover.visible is True


def test_debug_preview_panel_apply_locale_refreshes_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = DebugPreviewPanel(**_callbacks([]))
    monkeypatch.setattr(panel_module, "t", lambda key: f"label:{key}")

    panel.apply_locale()

    assert _button_label(panel._toggle_button) == "label:debug_preview.button"
    assert panel._toggle_button.tooltip == "label:debug_preview.tooltip"
    assert (
        _button_label(panel._action_buttons["brake_notice"]) == "label:debug_preview.brake_notice"
    )
    assert (
        _button_label(panel._action_buttons["discord_auth"]) == "label:debug_preview.discord_auth"
    )
    assert (
        _button_label(panel._action_buttons["peer_translation_eula"])
        == "label:debug_preview.peer_translation_eula"
    )


def test_debug_preview_panel_uses_text_button_label_api_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    class TextOnlyButton:
        def __init__(self, *, text, tooltip=None, on_click=None, style=None) -> None:
            self.text = text
            self.tooltip = tooltip
            self.on_click = on_click
            self.style = style
            created.append(self)

    monkeypatch.setattr(panel_module.ft, "TextButton", TextOnlyButton)
    panel = DebugPreviewPanel(**_callbacks([]))

    assert [button.text for button in created] == [
        "DBG",
        "Brake notice",
        "Revoked notice",
        "Founder letter",
        "PKCE failure",
        "Discord auth",
        "Peer translation EULA",
    ]

    monkeypatch.setattr(panel_module, "t", lambda key: f"label:{key}")
    panel.apply_locale()

    assert panel._toggle_button.text == "label:debug_preview.button"
    assert panel._action_buttons["brake_notice"].text == "label:debug_preview.brake_notice"
    assert panel._action_buttons["discord_auth"].text == "label:debug_preview.discord_auth"
    assert (
        panel._action_buttons["peer_translation_eula"].text
        == "label:debug_preview.peer_translation_eula"
    )


def test_debug_preview_i18n_keys_exist_in_all_locale_bundles() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    i18n_dir = repo_root / "src" / "puripuly_heart" / "data" / "i18n"

    for locale in ("en.json", "ko.json", "zh-CN.json"):
        bundle = json.loads((i18n_dir / locale).read_text(encoding="utf-8"))
        missing = DEBUG_PREVIEW_I18N_KEYS - set(bundle)
        assert not missing, f"{locale} missing {sorted(missing)}"


def test_debug_preview_panel_has_no_external_state_imports() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source = (
        repo_root / "src" / "puripuly_heart" / "ui" / "components" / "debug_preview_panel.py"
    ).read_text(encoding="utf-8")

    forbidden_fragments = [
        "puripuly_heart.config.settings",
        "puripuly_heart.core.openrouter",
        "puripuly_heart.core.managed_openrouter",
        "SecretStore",
        "secrets",
        "broker",
        "webbrowser",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in source


def test_agents_documents_debug_preview_policy() -> None:
    policy = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "Debug UI preview mode may exist" in policy
    assert "must not persist settings" in policy
    assert "must remain hidden unless the explicit debug flag is enabled" in policy
