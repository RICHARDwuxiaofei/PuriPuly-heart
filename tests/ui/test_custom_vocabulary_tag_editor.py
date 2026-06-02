from __future__ import annotations

from importlib import import_module

import pytest

ft = pytest.importorskip("flet")


def _editor_class():
    try:
        module = import_module("puripuly_heart.ui.components.settings.custom_vocabulary_tag_editor")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in RED run
        pytest.fail(f"CustomVocabularyTagEditor module is missing: {exc}")

    try:
        return module.CustomVocabularyTagEditor
    except AttributeError:  # pragma: no cover - exercised in RED run
        pytest.fail("CustomVocabularyTagEditor class is missing")


def _make_editor():
    return _editor_class()()


def _chip_controls(editor) -> list[ft.Container]:
    return list(editor._chips_wrap.controls)


def _chip_term_text(chip: ft.Container) -> ft.Text:
    row = chip.content
    assert isinstance(row, ft.Row)
    term_text = row.controls[0]
    assert isinstance(term_text, ft.Text)
    return term_text


def _chip_remove_button(chip: ft.Container) -> ft.IconButton:
    row = chip.content
    assert isinstance(row, ft.Row)
    button = row.controls[1]
    assert isinstance(button, ft.IconButton)
    return button


def test_set_terms_renders_one_wrapping_chip_per_term_and_hides_empty_state() -> None:
    editor = _make_editor()

    editor.set_terms(["Puripuly", "VRChat"])

    chips = _chip_controls(editor)
    assert editor._chips_wrap.wrap is True
    assert editor._empty_text.visible is False
    assert len(chips) == 2
    assert [_chip_term_text(chip).value for chip in chips] == ["Puripuly", "VRChat"]
    assert [chip.data for chip in chips] == ["Puripuly", "VRChat"]


def test_set_terms_renders_quiet_empty_state_for_empty_terms() -> None:
    editor = _make_editor()
    editor.set_empty_text("No hints yet.")

    editor.set_terms([])

    assert _chip_controls(editor) == []
    assert editor._empty_text.value == "No hints yet."
    assert editor._empty_text.visible is True
    assert editor._empty_text.color is not None


def test_visible_add_control_splits_commas_and_newlines_into_raw_submitted_values() -> None:
    editor = _make_editor()
    added: list[list[str]] = []
    editor.on_add_terms = added.append
    editor._input_field.value = "  Puripuly,VRChat\nSoniox  "

    assert editor._add_row.controls == [editor._input_field, editor._add_button]
    editor._add_button.on_click(None)

    assert added == [["  Puripuly", "VRChat", "Soniox  "]]
    assert editor._input_field.value == ""


def test_visible_add_control_ignores_empty_delimiter_segments_without_trimming_raw_values() -> None:
    editor = _make_editor()
    added: list[list[str]] = []
    editor.on_add_terms = added.append
    editor._input_field.value = "Puripuly,, VRChat\n\n Soniox"

    editor._add_button.on_click(None)

    assert added == [["Puripuly", " VRChat", " Soniox"]]


def test_remove_control_calls_back_with_visible_term() -> None:
    editor = _make_editor()
    removed: list[str] = []
    editor.on_remove_term = removed.append
    editor.set_remove_label_template("Remove {term}")
    editor.set_terms(["Puripuly"])

    remove_button = _chip_remove_button(_chip_controls(editor)[0])
    remove_button.on_click(None)

    assert removed == ["Puripuly"]
    assert remove_button.tooltip == "Remove Puripuly"


def test_locale_setters_update_placeholder_empty_add_and_existing_remove_labels() -> None:
    editor = _make_editor()
    editor.set_terms(["Puripuly"])

    editor.set_placeholder("힌트 추가")
    editor.set_empty_text("아직 추가된 힌트가 없어요.")
    editor.set_add_label("추가")
    editor.set_remove_label_template("{term} 삭제")

    assert editor._input_field.hint_text == "힌트 추가"
    assert editor._empty_text.value == "아직 추가된 힌트가 없어요."
    assert editor._add_button.text == "추가"
    assert _chip_remove_button(_chip_controls(editor)[0]).tooltip == "Puripuly 삭제"


def test_clear_input_clears_unsubmitted_add_text() -> None:
    editor = _make_editor()
    editor._input_field.value = "draft hint"

    editor.clear_input()

    assert editor._input_field.value == ""


def test_long_hint_text_is_constrained_and_available_as_tooltip() -> None:
    editor = _make_editor()
    long_term = "A very long Speech Recognition Hint " * 8

    editor.set_terms([long_term])

    chip = _chip_controls(editor)[0]
    term_text = _chip_term_text(chip)

    assert chip.clip_behavior == ft.ClipBehavior.HARD_EDGE
    assert term_text.value == long_term
    assert term_text.tooltip == long_term
    assert term_text.width is not None
    assert term_text.width <= 240
    assert term_text.max_lines == 1
    assert term_text.overflow == ft.TextOverflow.ELLIPSIS


def test_component_is_exported_from_settings_components_package() -> None:
    editor_class = _editor_class()
    settings_components = import_module("puripuly_heart.ui.components.settings")

    assert settings_components.CustomVocabularyTagEditor is editor_class
    assert "CustomVocabularyTagEditor" in settings_components.__all__
