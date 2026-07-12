from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.adapters.ui_settings_interactions import (
    PRODUCTION_CAPTURE_PRESENTATION_STATUSES,
    PRODUCTION_MANAGED_PRESENTATION_STATES,
    PRODUCTION_UI_INTERACTION_DETAIL_CODES,
)
from puripuly_heart.app.ports.application_settings import (
    CaptureTargetValue,
    DesktopOverlayValue,
    JsonScalarEntry,
    LocalExtraBodyValue,
    OverlayCalibrationValue,
    SecretMetadata,
    SecretSourceStatus,
    SecretVerificationStatus,
    SettingsField,
    StringListMapValue,
    StringMapValue,
    TranslationFallbackValue,
)
from puripuly_heart.app.ports.provider_verifier import PROVIDER_VERIFICATION_STATUSES
from puripuly_heart.app.ports.ui_settings import (
    AudioDeviceOption,
    AudioDeviceQueryResult,
    AudioSettingsSnapshot,
    CaptureDiagnosticReason,
    CaptureRetryStatus,
    CaptureTargetOption,
    CaptureTargetSnapshot,
    CredentialMetadataSnapshot,
    InteractionStatus,
    LanguageSettingsSnapshot,
    ManagedActionStatus,
    ManagedPresentation,
    MicrophoneTestStatus,
    OscOutputSettingsSnapshot,
    OverlaySettingsSnapshot,
    PromptSettingsSnapshot,
    ProviderSettingsSnapshot,
    RuntimeFacts,
    SttSettingsSnapshot,
    TranslationSettingsSnapshot,
    UiClipboardTelemetrySnapshot,
    UiSettingsApplied,
    UiSettingsConflict,
    UiSettingsDegraded,
    UiSettingsSnapshot,
    VocabularyGroup,
)
from puripuly_heart.app.services.application_settings_codecs import FIELD_CODECS, CodecKind
from puripuly_heart.ui.i18n import get_locale, set_locale, t
from puripuly_heart.ui.views.application_settings import ApplicationSettingsView


def snapshot(revision: str = "r1", *, locale: str = "en") -> UiSettingsSnapshot:
    return UiSettingsSnapshot(
        TranslationSettingsSnapshot(model="gemma4", connection="managed"),
        ProviderSettingsSnapshot(openrouter_model="model-a"),
        PromptSettingsSnapshot(system_prompt="prompt-a"),
        SttSettingsSnapshot(self_provider="deepgram", peer_provider="qwen_asr"),
        LanguageSettingsSnapshot(source="en", target="ja"),
        AudioSettingsSnapshot(input_device="mic-a"),
        OverlaySettingsSnapshot(target="steamvr"),
        OscOutputSettingsSnapshot(host="127.0.0.1", port=9000),
        UiClipboardTelemetrySnapshot(locale=locale),
        CredentialMetadataSnapshot(),
        (),
        CaptureTargetSnapshot(status="ready"),
        ManagedPresentation(connection_state="connected"),
        RuntimeFacts(overlay_active=True, overlay_state="running"),
        revision,
        "o1",
    )


class Interactions:
    def __init__(self) -> None:
        self.secrets: list[tuple[str, str]] = []

    async def set_secret(self, key: str, value: str):
        self.secrets.append((key, value))


class Application:
    def __init__(self, result=None) -> None:
        self.current = snapshot()
        self.result = result
        self.deltas = []
        self.interactions = Interactions()

    async def snapshot(self):
        return self.current

    async def apply(self, delta):
        self.deltas.append(delta)
        return self.result or UiSettingsApplied(snapshot("r2"), ())

    def run_interaction(self, operation):
        return operation


@pytest.mark.asyncio
async def test_snapshot_edits_mixed_submit_and_secret_redaction() -> None:
    application = Application()
    view = ApplicationSettingsView(application)
    await view.load()
    assert view.tab_order == ("api", "general", "prompt", "overlay")
    assert view.controls_by_field[SettingsField.TRANSLATION_MODEL].value == "gemma4"
    view.edit(SettingsField.TRANSLATION_MODEL, "deepseek_v4_flash")
    view.edit(SettingsField.SYSTEM_PROMPT, "prompt-b")
    view.secret_entries["openrouter_api_key"] = "raw-secret"
    assert application.deltas == []
    result = await view.submit()
    assert result.snapshot.canonical_revision == "r2"
    assert application.deltas[0].expected_revision == "r1"
    assert {change.field for change in application.deltas[0].changes} == {
        SettingsField.TRANSLATION_MODEL,
        SettingsField.SYSTEM_PROMPT,
    }
    assert application.interactions.secrets == [("openrouter_api_key", "raw-secret")]
    assert view.secret_entries == {}
    assert "raw-secret" not in repr(result)


@pytest.mark.asyncio
async def test_conflict_renders_authoritative_snapshot_and_runtime_only_state() -> None:
    authoritative = snapshot("r3")
    application = Application(UiSettingsConflict(authoritative, "r1", "r3"))
    view = ApplicationSettingsView(application)
    await view.load()
    view.edit(SettingsField.UI_LOCALE, "ja")
    await view.submit()
    assert view.draft.expected_revision == "r3"
    assert view.status_text.value == t("settings.application.conflict")
    assert view.runtime_text.value == t(
        "settings.application.runtime_status",
        status=t("settings.application.status.running"),
    )
    assert SettingsField.OVERLAY_TARGET not in {
        change.field for change in view.draft.delta().changes
    }


def test_import_boundary_excludes_legacy_and_runtime_owners() -> None:
    path = Path("src/puripuly_heart/ui/views/application_settings.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden = {
        "puripuly_heart.ui.views.settings",
        "puripuly_heart.config.settings",
        "puripuly_heart.app.wiring",
        "puripuly_heart.core.secret_store",
    }
    assert imports.isdisjoint(forbidden)
    assert "sounddevice" not in imports
    assert "pyaudiowpatch" not in imports


def test_every_committed_field_and_interaction_has_an_active_control() -> None:
    view = ApplicationSettingsView(Application())
    assert set(view.controls_by_field) == set(SettingsField)
    assert set(view.secret_controls) == {
        "google_api_key",
        "openrouter_api_key",
        "deepseek_api_key",
        "deepgram_api_key",
        "soniox_api_key",
        "alibaba_api_key_beijing",
        "alibaba_api_key_singapore",
        "alibaba_api_key",
        "local_llm_api_key",
        "cerebras_api_key",
    }
    assert {
        "pkce",
        "pkce_reopen",
        "pkce_cancel",
        "audio_refresh",
        "capture_refresh",
        "telemetry",
    } <= set(view.interaction_controls)


@pytest.mark.asyncio
async def test_visible_apply_control_submits_dirty_control_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = Application()
    view = ApplicationSettingsView(application)
    await view.load()

    class Page:
        def __init__(self) -> None:
            self.tasks = []

        def run_task(self, callback):
            task = asyncio.create_task(callback())
            self.tasks.append(task)
            return task

    page = Page()
    monkeypatch.setattr(type(view), "page", property(lambda _self: page))
    monkeypatch.setattr(view, "update", lambda: None)
    control = view.controls_by_field[SettingsField.TRANSLATION_MODEL]
    control.value = "deepseek_v4_flash"
    control.on_change(type("Event", (), {"control": control})())
    assert view.apply_button.disabled is False
    view.apply_button.on_click(None)
    await page.tasks[0]
    assert application.deltas[0].changes[0].field == SettingsField.TRANSLATION_MODEL
    assert view.apply_button.disabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "severity", "key"),
    (
        (UiSettingsApplied(snapshot("r2"), ()), "success", "settings.application.applied"),
        (
            UiSettingsConflict(snapshot("r3"), "r1", "r3"),
            "warning",
            "settings.application.conflict",
        ),
        (
            UiSettingsDegraded(snapshot("r2"), "degraded", ()),
            "error",
            "settings.application.degraded",
        ),
    ),
)
async def test_submit_feedback_is_localized_and_typed(result, severity, key) -> None:
    feedback = []
    application = Application(result)
    view = ApplicationSettingsView(
        application, on_snackbar=lambda message, level: feedback.append((message, level))
    )
    await view.load()
    view.edit(SettingsField.UI_LOCALE, "ja")
    await view.submit()
    assert feedback == [(t(key), severity)]


def test_production_constructs_only_application_settings_view() -> None:
    app_path = Path("src/puripuly_heart/ui/app.py")
    main_path = Path("src/puripuly_heart/main.py")
    app_source = app_path.read_text(encoding="utf-8")
    main_source = main_path.read_text(encoding="utf-8")
    assert "from puripuly_heart.ui.views.settings import SettingsView" not in app_source
    assert "ApplicationSettingsView(self.ui_settings)" in app_source
    assert "runtime_composition.ui_settings" in main_source
    for callback in (
        "on_settings_changed =",
        "on_prompt_apply_settings =",
        "on_providers_changed =",
        "on_request_openrouter_pkce =",
        "on_verify_api_key =",
        "on_telemetry_consent_change =",
    ):
        assert callback not in app_source


def _event_value(field: SettingsField):  # noqa: ANN202
    codec = FIELD_CODECS[field]
    if codec.choices:
        return sorted(codec.choices)[0]
    return {
        CodecKind.TEXT: "value",
        CodecKind.EMPTY_TEXT: "value",
        CodecKind.OPTIONAL_TEXT: None,
        CodecKind.BOOLEAN: True,
        CodecKind.INTEGER: max(1, int(codec.minimum or 1)),
        CodecKind.NUMBER: max(0.5, float(codec.minimum or 0.5)),
        CodecKind.STRING_LIST: ("en",),
        CodecKind.STRING_MAP: StringMapValue((("gemma4", "managed"),)),
        CodecKind.STRING_LIST_MAP: StringListMapValue((("en", ("airi",)),)),
        CodecKind.LOCAL_EXTRA_BODY: LocalExtraBodyValue(()),
        CodecKind.FALLBACK: TranslationFallbackValue(
            True, "gemma4", "openrouter", "openrouter_gemma4_26b_a4b"
        ),
        CodecKind.CALIBRATION: OverlayCalibrationValue(),
        CodecKind.DESKTOP_OVERLAY: DesktopOverlayValue(),
        CodecKind.CAPTURE_TARGET: CaptureTargetValue(),
    }[codec.kind]


def _event(control):  # noqa: ANN202
    return type("Event", (), {"control": control})()


def _descendants(control):  # noqa: ANN202
    for child in getattr(control, "controls", ()):
        yield child
        yield from _descendants(child)
    content = getattr(control, "content", None)
    if content is not None:
        yield content
        yield from _descendants(content)


def _change_composite_child(control, field: SettingsField) -> None:
    if field in {
        SettingsField.PEER_EXPECTED_LANGUAGES,
        SettingsField.RECENT_SOURCE_LANGUAGES,
        SettingsField.RECENT_TARGET_LANGUAGES,
    }:
        child, add = control.controls[2].controls
        before_typing = control.value
        for value in ("f", "fr"):
            child.value = value
            child.on_change(_event(child))
            assert control.value == before_typing
        child.on_submit(_event(child))
        control.controls[1].controls[-1].content.controls[1].on_click(None)
        child.value = "de"
        child.on_change(_event(child))
        add.on_click(None)
    elif field in {
        SettingsField.TRANSLATION_CONNECTION_HISTORY,
        SettingsField.OPENROUTER_PROVIDER_ROUTING,
    }:
        control.controls[2].on_click(None)
        row = control.controls[1].controls[-1]
        key, value = row.controls[:2]
        key.value = "deepseek_v4_flash"
        key.on_change(_event(key))
        value.value = "managed"
        value.on_change(_event(value))
        row.controls[2].on_click(None)
        control.controls[2].on_click(None)
        row = control.controls[1].controls[-1]
        key, value = row.controls[:2]
        key.value = "deepseek_v4_flash"
        key.on_change(_event(key))
        value.value = "managed"
        value.on_change(_event(value))
    elif field is SettingsField.STT_CUSTOM_TERMS:
        control.controls[2].on_click(None)
        language = control.controls[1].controls[-1].controls[0].controls[0]
        language.value = "ja"
        language.on_change(_event(language))
        control.controls[1].controls[-1].controls[0].controls[1].on_click(None)
        control.controls[2].on_click(None)
        language = control.controls[1].controls[-1].controls[0].controls[0]
        language.value = "ja"
        language.on_change(_event(language))
    elif field is SettingsField.LOCAL_LLM_EXTRA_BODY:
        control.controls[2].on_click(None)
        row = control.controls[1].controls[-1]
        key, kind, value = row.controls[1:4]
        key.value = "temperature"
        key.on_change(_event(key))
        kind.value = "number"
        kind.on_change(_event(kind))
        value.value = "0.4"
        value.on_change(_event(value))
        row.controls[4].on_click(None)
        control.controls[2].on_click(None)
        row = control.controls[1].controls[-1]
        key, kind, value = row.controls[1:4]
        key.value = "temperature"
        key.on_change(_event(key))
        kind.value = "number"
        kind.on_change(_event(kind))
        value.value = "0.4"
        value.on_change(_event(value))
    elif field is SettingsField.TRANSLATION_FALLBACK:
        enabled, model, connection, alias = control.controls[1:]
        enabled.value = True
        enabled.on_change(_event(enabled))
        model.value = "gemma4"
        model.on_change(_event(model))
        connection.value = "openrouter"
        connection.on_change(_event(connection))
        alias.value = "openrouter_gemma4_26b_a4b"
        alias.on_change(_event(alias))
    elif field is SettingsField.DESKTOP_AUDIO_CAPTURE_TARGET:
        kind, _device, process_kind, identity, _channel = control.controls[1:]
        identity.value = "C:\\Apps\\player.exe"
        identity.on_change(_event(identity))
        process_kind.value = "generic_executable"
        process_kind.on_change(_event(process_kind))
        kind.value = "process"
        kind.on_change(_event(kind))
    elif field is SettingsField.OVERLAY_CALIBRATION:
        anchor, offset_x, offset_y, distance, text_scale, alpha = control.controls[1].controls
        for child, value in (
            (anchor, "head_locked"),
            (offset_x, "0.1"),
            (offset_y, "-0.2"),
            (distance, "1.2"),
            (text_scale, "1.1"),
            (alpha, "0.3"),
        ):
            child.value = value
            child.on_change(_event(child))
    elif field is SettingsField.OVERLAY_DESKTOP_FLET:
        size, x, y, lock, alpha = control.controls[1].controls
        for child, value in ((size, "large"), (x, "12"), (y, "24"), (alpha, "0.5")):
            child.value = value
            child.on_change(_event(child))
        lock.value = False
        lock.on_change(_event(lock))
    else:
        raise AssertionError(field)


@pytest.mark.parametrize("field", tuple(SettingsField))
def test_every_field_control_event_builds_a_real_codec_valid_delta(field: SettingsField) -> None:
    view = ApplicationSettingsView(Application())
    view.render(snapshot())
    control = view.controls_by_field[field]
    value = _event_value(field)
    descriptor = view.catalog.field(field.value)
    assert descriptor is not None
    if descriptor.editor.value in {
        "string_list",
        "string_map",
        "string_list_map",
        "json_scalar_map",
        "fallback",
        "calibration",
        "desktop_overlay",
        "capture_target",
    }:
        _change_composite_child(control, field)
    else:
        control.value = "" if value is None else value
        control.on_change(type("Event", (), {"control": control})())
    delta = view.draft.delta()
    assert len(delta.changes) == 1
    change = delta.changes[0]
    assert change.field is field
    encoded = FIELD_CODECS[field].encode(change.value)
    assert (
        FIELD_CODECS[field].encode(FIELD_CODECS[field].decode(tuple(item for _, item in encoded)))
        == encoded
    )


_COMPOSITE_FIELDS = tuple(
    field
    for field, codec in FIELD_CODECS.items()
    if codec.kind
    in {
        CodecKind.STRING_LIST,
        CodecKind.STRING_MAP,
        CodecKind.STRING_LIST_MAP,
        CodecKind.LOCAL_EXTRA_BODY,
        CodecKind.FALLBACK,
        CodecKind.CALIBRATION,
        CodecKind.DESKTOP_OVERLAY,
        CodecKind.CAPTURE_TARGET,
    }
)


def _composite_snapshot() -> UiSettingsSnapshot:
    base = snapshot()
    return replace(
        base,
        translation=replace(
            base.translation,
            connection_history=(("gemma4", "managed"),),
            fallback=TranslationFallbackValue(
                True, "gemma4", "openrouter", "openrouter_gemma4_26b_a4b"
            ),
        ),
        providers=replace(
            base.providers,
            local_extra_body=(JsonScalarEntry("temperature", 0.2),),
        ),
        languages=replace(
            base.languages,
            peer_expected=("en",),
            recent_source=("ja",),
            recent_target=("ko",),
        ),
        stt=replace(base.stt, custom_terms=(VocabularyGroup("en", ("airi",)),)),
        audio=replace(base.audio, capture_target=CaptureTargetValue()),
        overlay=replace(
            base.overlay,
            calibration=OverlayCalibrationValue(),
            desktop=DesktopOverlayValue(),
        ),
    )


@pytest.mark.parametrize("field", _COMPOSITE_FIELDS)
def test_composite_editor_interaction_render_and_authoritative_resync(field: SettingsField) -> None:
    authoritative = _composite_snapshot()
    view = ApplicationSettingsView(Application())
    view.render(authoritative)
    control = view.controls_by_field[field]
    original = control.value
    FIELD_CODECS[field].encode(original)
    _change_composite_child(control, field)
    changed = control.value
    assert view.draft.delta().changes[0].value == changed
    view.render(authoritative)
    assert control.value == original
    assert view.draft.delta().changes == ()


@pytest.mark.parametrize("field", _COMPOSITE_FIELDS)
def test_mounted_composite_child_events_rebuild_dirty_and_codec_validate(
    field: SettingsField, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = ApplicationSettingsView(Application())
    view.render(_composite_snapshot())
    control = view.controls_by_field[field]
    updates = []
    monkeypatch.setattr(type(control), "page", property(lambda _self: object()))
    monkeypatch.setattr(type(control), "update", lambda _self: updates.append("updated"))
    _change_composite_child(control, field)
    assert view.apply_button.disabled is False
    delta = view.draft.delta()
    assert len(delta.changes) == 1
    change = delta.changes[0]
    assert change.field is field
    encoded = FIELD_CODECS[field].encode(change.value)
    assert (
        FIELD_CODECS[field].encode(FIELD_CODECS[field].decode(tuple(item for _, item in encoded)))
        == encoded
    )


@pytest.mark.parametrize(
    "field",
    (
        SettingsField.PEER_EXPECTED_LANGUAGES,
        SettingsField.RECENT_SOURCE_LANGUAGES,
        SettingsField.RECENT_TARGET_LANGUAGES,
    ),
)
@pytest.mark.parametrize("commit", ("submit", "add"))
def test_mounted_string_list_typing_commits_once_and_codec_validates(
    field: SettingsField, commit: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = ApplicationSettingsView(Application())
    view.render(snapshot())
    editor = view.controls_by_field[field]
    monkeypatch.setattr(type(editor), "page", property(lambda _self: object()))
    monkeypatch.setattr(type(editor), "update", lambda _self: None)
    input_control, add_button = editor.controls[2].controls
    original = editor.value
    for partial in ("f", "fr", "fra"):
        input_control.value = partial
        input_control.on_change(_event(input_control))
        assert editor.value == original
        assert view.draft.delta().changes == ()
    input_control.value = "fr"
    if commit == "submit":
        input_control.on_submit(_event(input_control))
    else:
        add_button.on_click(None)
    assert input_control.value == ""
    delta = view.draft.delta()
    assert len(delta.changes) == 1
    change = delta.changes[0]
    assert change.field is field
    assert change.value == ("fr",)
    encoded = FIELD_CODECS[field].encode(change.value)
    assert (
        FIELD_CODECS[field].encode(FIELD_CODECS[field].decode(tuple(item for _, item in encoded)))
        == encoded
    )


@pytest.mark.parametrize("locale", ("ja", "ko", "ru", "zh-CN"))
def test_full_registry_locale_refresh_has_no_semantic_ids_and_preserves_draft(
    locale: str,
) -> None:
    original_locale = get_locale()
    try:
        set_locale("en")
        view = ApplicationSettingsView(Application())
        view.render(_composite_snapshot())
        view.edit(SettingsField.SYSTEM_PROMPT, "ephemeral draft")
        view.secret_entries["openrouter_api_key"] = "ephemeral secret"
        view.secret_controls["openrouter_api_key"].value = "ephemeral secret"
        before = view.draft.delta()
        english = {
            field: getattr(control, "label", None)
            for field, control in view.controls_by_field.items()
        }
        view.refresh_locale(locale)
        assert view.draft.delta() == before
        assert view.secret_entries["openrouter_api_key"] == "ephemeral secret"
        assert view.secret_controls["openrouter_api_key"].value == "ephemeral secret"
        assert view.apply_button.disabled is False
        expected_translation_labels = {
            "ja": "翻訳モデル",
            "ko": "번역 모델",
            "ru": "Модель перевода",
            "zh-CN": "翻译模型",
        }
        expected_capture_labels = {
            "ja": "現在のキャプチャ対象",
            "ko": "현재 캡처 대상",
            "ru": "Текущий источник захвата",
            "zh-CN": "当前捕获目标",
        }
        assert (
            view.controls_by_field[SettingsField.TRANSLATION_MODEL].label
            == expected_translation_labels[locale]
        )
        assert view.capture_target_control.label == expected_capture_labels[locale]
        assert view.telemetry_control.label == t("settings.application.telemetry")
        for field, control in view.controls_by_field.items():
            label = getattr(control, "label", None)
            if label is not None:
                assert label != field.value
                assert not label.startswith("settings.")
            for option in getattr(control, "options", ()):
                assert option.text != option.key
                assert not option.text.startswith("settings.")
            if hasattr(control, "refresh_locale"):
                assert not control.controls[0].value.startswith("settings.")
                for child in _descendants(control):
                    child_label = getattr(child, "label", None)
                    if child_label:
                        assert not child_label.startswith("settings.")
                    child_text = getattr(child, "text", None)
                    if child_text:
                        assert not child_text.startswith("settings.")
                    for option in getattr(child, "options", ()):
                        assert option.text != option.key
                        assert not option.text.startswith("settings.")
        assert any(
            getattr(view.controls_by_field[field], "label", None) != label
            for field, label in english.items()
            if label is not None
        )
        assert all(
            not control.text.startswith("settings.")
            for control in view.interaction_controls.values()
        )
        assert all(
            not control.label.startswith("settings.") for control in view.secret_controls.values()
        )
        for status in (
            view.managed_text,
            view.capture_status,
            view.runtime_text,
            view.telemetry_status,
        ):
            assert not status.value.startswith("settings.")
    finally:
        set_locale(original_locale)


def test_all_production_interaction_codes_have_semantic_presentations() -> None:
    unknown_status = t("settings.application.status.unknown")
    statuses = {
        *(item.value for item in InteractionStatus),
        *(item.value for item in ManagedActionStatus),
        *(item.value for item in CaptureRetryStatus),
        *(item.value for item in MicrophoneTestStatus),
        *PROVIDER_VERIFICATION_STATUSES,
        "off",
        "active",
        "starting",
        "connected",
        "stopping",
        "reopened",
        "inactive",
        "closed",
        "committed_degraded",
    }
    assert all(ApplicationSettingsView._status_label(value) != unknown_status for value in statuses)
    unknown_detail = t("settings.application.detail.unknown")
    assert all(
        ApplicationSettingsView._interaction_detail_label(value) != unknown_detail
        for value in PRODUCTION_UI_INTERACTION_DETAIL_CODES
    )
    assert all(
        ApplicationSettingsView._capture_status_label(value) != unknown_status
        for value in PRODUCTION_CAPTURE_PRESENTATION_STATUSES
    )
    assert all(
        ApplicationSettingsView._capture_reason_label(value.value) != unknown_status
        for value in CaptureDiagnosticReason
    )
    assert all(
        ApplicationSettingsView._managed_state_label(value)
        != t("settings.application.managed_state.unknown")
        for value in PRODUCTION_MANAGED_PRESENTATION_STATES - {"unknown"}
    )
    view = ApplicationSettingsView(Application())
    assert set(view.credential_provider_ids) == set(view.secret_controls)
    assert all(
        ApplicationSettingsView._provider_label(value) != t("settings.application.provider.unknown")
        for value in view.credential_provider_ids.values()
    )
    assert view.credential_provider_ids["alibaba_api_key_beijing"] == "alibaba_beijing"
    assert view.credential_provider_ids["alibaba_api_key_singapore"] == "alibaba_singapore"


def test_runtime_audio_and_process_option_labels_remain_verbatim_across_locale_refresh() -> None:
    original_locale = get_locale()
    try:
        set_locale("en")
        view = ApplicationSettingsView(Application())
        view.render(snapshot())
        view._render_audio(
            AudioDeviceQueryResult(
                InteractionStatus.APPLIED,
                ("My Host API",),
                (AudioDeviceOption("usb-mic", "My Host API", "My USB Microphone"),),
                (AudioDeviceOption("loopback", "loopback", "Studio Loopback"),),
            )
        )
        view._render_capture(
            CaptureTargetSnapshot(
                selected_id="process:player",
                options=(
                    CaptureTargetOption("process:player", "process", "My Game Process", True),
                ),
                status="available",
            )
        )
        view.edit(SettingsField.SYSTEM_PROMPT, "dirty")
        view.refresh_locale("ja")
        assert [
            option.text
            for option in view.controls_by_field[SettingsField.AUDIO_INPUT_HOST_API].options
        ] == ["My Host API"]
        assert [
            option.text
            for option in view.controls_by_field[SettingsField.AUDIO_INPUT_DEVICE].options
        ] == ["My USB Microphone"]
        assert [
            option.text
            for option in view.controls_by_field[SettingsField.DESKTOP_AUDIO_OUTPUT_DEVICE].options
        ] == ["Studio Loopback"]
        assert [option.text for option in view.capture_target_control.options] == [
            "My Game Process"
        ]
    finally:
        set_locale(original_locale)


def test_startup_snapshot_forces_full_locale_refresh_after_global_locale_changed() -> None:
    original_locale = get_locale()
    try:
        set_locale("ja")
        view = ApplicationSettingsView(Application())
        assert view.controls_by_field[SettingsField.TRANSLATION_MODEL].label == "翻訳モデル"
        set_locale("ko")
        view.render(snapshot(locale="ko"))
        assert view.controls_by_field[SettingsField.TRANSLATION_MODEL].label == "번역 모델"
        assert view.capture_target_control.label == "현재 캡처 대상"
        assert view.subtabs.button_by_key["general"].text == t("settings.tab.general")
        assert view.secret_controls["openrouter_api_key"].label == t("settings.openrouter_api_key")
        for field, control in view.controls_by_field.items():
            if hasattr(control, "label"):
                assert control.label == view._field_label(field)
        for key, label_key in zip(
            view.tab_order,
            (
                "settings.tab.api",
                "settings.tab.general",
                "settings.tab.prompt",
                "settings.tab.overlay",
            ),
            strict=True,
        ):
            assert view.subtabs.button_by_key[key].text == t(label_key)
    finally:
        set_locale(original_locale)


def test_authoritative_credentials_runtime_and_active_capture_render_resync_and_localize() -> None:
    original_locale = get_locale()
    try:
        set_locale("en")
        base = snapshot()
        entries = tuple(
            SecretMetadata(
                key,
                index % 2 == 0,
                f"secret-{index}",
                (
                    SecretVerificationStatus.VERIFIED
                    if index % 2 == 0
                    else SecretVerificationStatus.FAILED
                ),
                (SecretSourceStatus.ENVIRONMENT if index % 2 == 0 else SecretSourceStatus.KEYRING),
            )
            for index, key in enumerate(
                (
                    "google_api_key",
                    "openrouter_api_key",
                    "deepseek_api_key",
                    "deepgram_api_key",
                    "soniox_api_key",
                    "alibaba_api_key_beijing",
                    "alibaba_api_key_singapore",
                    "alibaba_api_key",
                    "local_llm_api_key",
                    "cerebras_api_key",
                )
            )
        )
        authoritative = replace(
            base,
            credentials=CredentialMetadataSnapshot(entries),
            runtime=RuntimeFacts(
                overlay_active=True,
                peer_active=True,
                overlay_state="connected",
                microphone_test_active=True,
            ),
            capture=CaptureTargetSnapshot(
                selected_id="target:selected",
                active_id="target:active",
                options=(
                    CaptureTargetOption("target:selected", "process", "Selected App"),
                    CaptureTargetOption("target:active", "process", "Active App"),
                ),
                status="available",
            ),
        )
        view = ApplicationSettingsView(Application())
        view.render(authoritative)
        assert set(view.credential_metadata_controls) == {entry.key for entry in entries}
        assert "Configured" in view.credential_metadata_controls["google_api_key"].value
        assert "Environment variable" in view.credential_metadata_controls["google_api_key"].value
        assert "Verified" in view.credential_metadata_controls["google_api_key"].value
        assert "Not configured" in view.credential_metadata_controls["openrouter_api_key"].value
        assert "Peer translation: Active" == view.peer_runtime_text.value
        assert "Overlay runtime: Active" == view.overlay_active_text.value
        assert "Microphone test: Active" == view.microphone_runtime_text.value
        assert view.capture_target_control.value == "target:selected"
        assert "Selected App" in view.capture_active_status.value
        assert "Active App" in view.capture_active_status.value
        rendered = (
            view.peer_runtime_text.value,
            view.overlay_active_text.value,
            view.microphone_runtime_text.value,
            view.capture_active_status.value,
        )
        view.edit(SettingsField.SYSTEM_PROMPT, "inert draft")
        assert rendered == (
            view.peer_runtime_text.value,
            view.overlay_active_text.value,
            view.microphone_runtime_text.value,
            view.capture_active_status.value,
        )
        view.refresh_locale("ja")
        assert "設定済み" in view.credential_metadata_controls["google_api_key"].value
        assert "環境変数" in view.credential_metadata_controls["google_api_key"].value
        assert "実行中" in view.peer_runtime_text.value
        assert "選択中: Selected App" in view.capture_active_status.value
        replacement = replace(
            authoritative,
            runtime=RuntimeFacts(overlay_state="off"),
            capture=CaptureTargetSnapshot(
                selected_id="target:selected",
                active_id=None,
                options=authoritative.capture.options,
                status="available",
            ),
            canonical_revision="r2",
        )
        view.render(replacement)
        assert t("settings.application.status.inactive") in view.peer_runtime_text.value
        assert t("settings.application.capture.none") in view.capture_active_status.value
        assert view.draft.delta().changes == ()
    finally:
        set_locale(original_locale)


def test_composite_editor_copy_is_meaningfully_translated_in_every_locale() -> None:
    keys = {
        "settings.application.add",
        "settings.application.item",
        "settings.application.key",
        "settings.application.value",
        "settings.application.language",
        "settings.application.enabled",
        "settings.application.model",
        "settings.application.connection",
        "settings.application.selection_alias",
        "settings.application.kind",
        "settings.application.device",
        "settings.application.process_kind",
        "settings.application.process_identity",
        "settings.application.discord_channel",
        "settings.application.anchor",
        "settings.application.offset_x",
        "settings.application.offset_y",
        "settings.application.distance",
        "settings.application.text_scale",
        "settings.application.background_alpha",
        "settings.application.size",
        "settings.application.position_x",
        "settings.application.position_y",
        "settings.application.position_lock",
        "settings.application.type",
    }
    directory = Path("src/puripuly_heart/data/i18n")
    bundles = {
        locale: json.loads((directory / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in ("en", "ja", "ko", "ru", "zh-CN")
    }
    for locale in ("ja", "ko", "ru", "zh-CN"):
        for key in keys:
            assert bundles[locale][key].strip()
            assert bundles[locale][key] != bundles["en"][key]
