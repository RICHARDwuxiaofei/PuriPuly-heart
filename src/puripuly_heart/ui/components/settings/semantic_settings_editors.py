from __future__ import annotations

from collections.abc import Callable

import flet as ft

from puripuly_heart.app.ports.application_settings import (
    CaptureTargetValue,
    DesktopOverlayValue,
    JsonScalarEntry,
    LocalExtraBodyValue,
    OverlayCalibrationValue,
    StringListMapValue,
    StringMapValue,
    TranslationFallbackValue,
)
from puripuly_heart.app.ports.ui_settings import SettingsOption, VocabularyGroup
from puripuly_heart.ui.components.settings.custom_vocabulary_tag_editor import (
    CustomVocabularyTagEditor,
)
from puripuly_heart.ui.i18n import t

Change = Callable[[object], None]


def _option(value: str) -> str:
    from puripuly_heart.ui.views.application_settings import ApplicationSettingsView

    return ApplicationSettingsView._option_label(value)


def _refresh(control: ft.Control) -> None:
    if control.page is not None:
        control.update()


def _text(label: str, value: object, changed) -> ft.TextField:
    return ft.TextField(label=label, value="" if value is None else str(value), on_change=changed)


def _number(label: str, value: object, changed) -> ft.TextField:
    return ft.TextField(
        label=label,
        value="" if value is None else str(value),
        keyboard_type=ft.KeyboardType.NUMBER,
        on_change=changed,
    )


def _dropdown(label: str, value: object, options: tuple[str, ...], changed) -> ft.Dropdown:
    return ft.Dropdown(
        label=label,
        value=None if value is None else str(value),
        options=[ft.dropdown.Option(key=item, text=_option(item)) for item in options],
        on_change=changed,
    )


class StringListEditor(ft.Column):
    def __init__(self, label: str, on_change: Change) -> None:
        self._label = label
        self._on_change = on_change
        self._values: list[str] = []
        self._input = _text(t("settings.application.item"), "", lambda _event: None)
        self._input.on_submit = self._add_from_event
        self._items = ft.Row(wrap=True)
        self._add = ft.OutlinedButton(
            text=t("settings.application.add"), on_click=lambda _event: self._add_input()
        )
        self._title = ft.Text(label)
        super().__init__([self._title, self._items, ft.Row([self._input, self._add])])

    def refresh_locale(self, label: str) -> None:
        self._title.value = label
        self._input.label = t("settings.application.item")
        self._add.text = t("settings.application.add")
        self._render()

    @property
    def value(self) -> tuple[str, ...]:
        return tuple(self._values)

    @value.setter
    def value(self, value: object) -> None:
        self.set_value(value)

    def set_value(self, value: object) -> None:
        self._values = list(value or ())
        self._render()

    def _add_from_event(self, event) -> None:
        value = (event.control.value or "").strip()
        if not value or value in self._values:
            return
        self._values.append(value)
        event.control.value = ""
        self._emit()

    def _add_input(self) -> None:
        self._add_from_event(type("InputEvent", (), {"control": self._input})())

    def _remove(self, value: str) -> None:
        self._values.remove(value)
        self._emit()

    def _emit(self) -> None:
        self._render()
        self._on_change(tuple(self._values))

    def _render(self) -> None:
        self._items.controls = [
            ft.Container(
                content=ft.Row(
                    [
                        ft.Text(value),
                        ft.IconButton(
                            ft.Icons.CLOSE,
                            on_click=lambda _e, item=value: self._remove(item),
                        ),
                    ],
                    tight=True,
                ),
                border=ft.border.all(1),
                border_radius=16,
                padding=ft.padding.only(left=8),
            )
            for value in self._values
        ]
        _refresh(self._items)


class StringMapEditor(ft.Column):
    def __init__(
        self,
        label: str,
        on_change: Change,
        *,
        key_options: tuple[str, ...] = (),
        value_options: tuple[str, ...] = (),
    ) -> None:
        self._label = label
        self._on_change = on_change
        self._key_options = key_options
        self._value_options = value_options
        self._entries: list[tuple[str, str]] = []
        self._rows = ft.Column()
        self._add = ft.OutlinedButton(
            text=t("settings.application.add"), on_click=lambda _e: self._append()
        )
        self._title = ft.Text(label)
        super().__init__([self._title, self._rows, self._add])

    def refresh_locale(self, label: str) -> None:
        self._title.value = label
        self._add.text = t("settings.application.add")
        self._render()

    @property
    def value(self) -> StringMapValue:
        return StringMapValue(tuple(self._entries))

    @value.setter
    def value(self, value: object) -> None:
        self.set_value(value)

    def set_value(self, value: object) -> None:
        entries = value.entries if isinstance(value, StringMapValue) else value or ()
        self._entries = [(str(key), str(item)) for key, item in entries]
        self._render()

    def _append(self) -> None:
        self._entries.append(
            (
                self._key_options[0] if self._key_options else "",
                self._value_options[0] if self._value_options else "",
            )
        )
        self._render()
        self._on_change(self.value)

    def _change(self, index: int, part: int, raw: str | None) -> None:
        entry = list(self._entries[index])
        entry[part] = raw or ""
        self._entries[index] = (entry[0], entry[1])
        self._on_change(self.value)

    def _remove(self, index: int) -> None:
        self._entries.pop(index)
        self._render()
        self._on_change(self.value)

    def _render(self) -> None:
        rows = []
        for index, (key, value) in enumerate(self._entries):
            key_control = (
                _dropdown(
                    t("settings.application.key"),
                    key,
                    self._key_options,
                    lambda e, i=index: self._change(i, 0, e.control.value),
                )
                if self._key_options
                else _text(
                    t("settings.application.key"),
                    key,
                    lambda e, i=index: self._change(i, 0, e.control.value),
                )
            )
            value_control = (
                _dropdown(
                    t("settings.application.value"),
                    value,
                    self._value_options,
                    lambda e, i=index: self._change(i, 1, e.control.value),
                )
                if self._value_options
                else _text(
                    t("settings.application.value"),
                    value,
                    lambda e, i=index: self._change(i, 1, e.control.value),
                )
            )
            rows.append(
                ft.Row(
                    [
                        key_control,
                        value_control,
                        ft.IconButton(
                            ft.Icons.DELETE, on_click=lambda _e, i=index: self._remove(i)
                        ),
                    ]
                )
            )
        self._rows.controls = rows
        _refresh(self._rows)


class CustomVocabularyEditor(ft.Column):
    def __init__(self, label: str, on_change: Change) -> None:
        self._label = label
        self._on_change = on_change
        self._groups: list[tuple[str, list[str]]] = []
        self._rows = ft.Column()
        self._title = ft.Text(label)
        self._add = ft.OutlinedButton(
            text=t("settings.application.add"), on_click=lambda _e: self._append()
        )
        super().__init__(
            [
                self._title,
                self._rows,
                self._add,
            ]
        )

    def refresh_locale(self, label: str) -> None:
        self._title.value = label
        self._add.text = t("settings.application.add")
        self._render()

    @property
    def value(self) -> StringListMapValue:
        return StringListMapValue(
            tuple((language, tuple(terms)) for language, terms in self._groups)
        )

    @value.setter
    def value(self, value: object) -> None:
        self.set_value(value)

    def set_value(self, value: object) -> None:
        if isinstance(value, StringListMapValue):
            entries = value.entries
        else:
            entries = tuple(
                (item.language, item.terms)
                for item in (value or ())
                if isinstance(item, VocabularyGroup)
            )
        self._groups = [(language, list(terms)) for language, terms in entries]
        self._render()

    def _append(self) -> None:
        self._groups.append(("", []))
        self._render()
        self._on_change(self.value)

    def _language(self, index: int, value: str | None) -> None:
        self._groups[index] = (value or "", self._groups[index][1])
        self._on_change(self.value)

    def _terms(self, index: int, terms: list[str]) -> None:
        self._groups[index] = (self._groups[index][0], terms)
        self._render()
        self._on_change(self.value)

    def _remove(self, index: int) -> None:
        self._groups.pop(index)
        self._render()
        self._on_change(self.value)

    def _render(self) -> None:
        rows = []
        for index, (language, terms) in enumerate(self._groups):
            tags = CustomVocabularyTagEditor(
                on_add_terms=lambda added, i=index: self._terms(i, [*self._groups[i][1], *added]),
                on_remove_term=lambda removed, i=index: self._terms(
                    i, [item for item in self._groups[i][1] if item != removed]
                ),
            )
            tags.set_terms(terms)
            rows.append(
                ft.Column(
                    [
                        ft.Row(
                            [
                                _text(
                                    t("settings.application.language"),
                                    language,
                                    lambda e, i=index: self._language(i, e.control.value),
                                ),
                                ft.IconButton(
                                    ft.Icons.DELETE, on_click=lambda _e, i=index: self._remove(i)
                                ),
                            ]
                        ),
                        tags,
                    ]
                )
            )
        self._rows.controls = rows
        _refresh(self._rows)


class TranslationFallbackEditor(ft.Column):
    def __init__(
        self,
        label: str,
        on_change: Change,
        models: tuple[SettingsOption, ...],
        connections: tuple[SettingsOption, ...],
        aliases: tuple[SettingsOption, ...],
    ) -> None:
        self._on_change = on_change
        self.enabled = ft.Switch(
            label=t("settings.application.enabled"), on_change=lambda _e: self._emit()
        )
        self.model = _dropdown(
            t("settings.application.model"),
            None,
            tuple(item.semantic_id for item in models),
            lambda _e: self._emit(),
        )
        self.connection = _dropdown(
            t("settings.application.connection"),
            None,
            tuple(item.semantic_id for item in connections),
            lambda _e: self._emit(),
        )
        self.alias = _dropdown(
            t("settings.application.selection_alias"),
            None,
            tuple(item.semantic_id for item in aliases),
            lambda _e: self._emit(),
        )
        self._title = ft.Text(label)
        super().__init__([self._title, self.enabled, self.model, self.connection, self.alias])

    def refresh_locale(self, label: str) -> None:
        self._title.value = label
        self.enabled.label = t("settings.application.enabled")
        for control, key in (
            (self.model, "model"),
            (self.connection, "connection"),
            (self.alias, "selection_alias"),
        ):
            control.label = t(f"settings.application.{key}")
            for option in control.options:
                option.text = _option(option.key)

    @property
    def value(self) -> TranslationFallbackValue:
        return TranslationFallbackValue(
            bool(self.enabled.value),
            self.model.value or "",
            self.connection.value or "",
            self.alias.value or "",
        )

    @value.setter
    def value(self, value: object) -> None:
        if not isinstance(value, TranslationFallbackValue):
            return
        self.enabled.value, self.model.value, self.connection.value, self.alias.value = (
            value.enabled,
            value.model,
            value.connection,
            value.selection_alias,
        )

    def _emit(self) -> None:
        try:
            self._on_change(self.value)
        except ValueError:
            return


class CaptureTargetEditor(ft.Column):
    def __init__(
        self,
        label: str,
        on_change: Change,
        kinds: tuple[SettingsOption, ...],
        process_kinds: tuple[SettingsOption, ...],
    ) -> None:
        self._on_change = on_change
        self.kind = _dropdown(
            t("settings.application.kind"),
            None,
            tuple(item.semantic_id for item in kinds),
            lambda _e: self._emit(),
        )
        self.device = _text(t("settings.application.device"), "", lambda _e: self._emit())
        self.process_kind = _dropdown(
            t("settings.application.process_kind"),
            None,
            tuple(item.semantic_id for item in process_kinds),
            lambda _e: self._emit(),
        )
        self.identity = _text(
            t("settings.application.process_identity"), "", lambda _e: self._emit()
        )
        self.channel = _text(t("settings.application.discord_channel"), "", lambda _e: self._emit())
        self._title = ft.Text(label)
        super().__init__(
            [self._title, self.kind, self.device, self.process_kind, self.identity, self.channel]
        )

    def refresh_locale(self, label: str) -> None:
        self._title.value = label
        for control, key in (
            (self.kind, "kind"),
            (self.device, "device"),
            (self.process_kind, "process_kind"),
            (self.identity, "process_identity"),
            (self.channel, "discord_channel"),
        ):
            control.label = t(f"settings.application.{key}")
        for control in (self.kind, self.process_kind):
            for option in control.options:
                option.text = _option(option.key)

    @property
    def value(self) -> CaptureTargetValue:
        return CaptureTargetValue(
            self.kind.value or "default_output_device",
            self.device.value or None,
            self.process_kind.value or None,
            self.identity.value or None,
            self.channel.value or None,
        )

    @value.setter
    def value(self, value: object) -> None:
        if not isinstance(value, CaptureTargetValue):
            return
        (
            self.kind.value,
            self.device.value,
            self.process_kind.value,
            self.identity.value,
            self.channel.value,
        ) = (
            value.kind,
            value.device_name or "",
            value.process_kind,
            value.executable_identity or "",
            value.discord_channel or "",
        )

    def _emit(self) -> None:
        try:
            self._on_change(self.value)
        except ValueError:
            return


class OverlayCalibrationEditor(ft.Column):
    def __init__(self, label: str, on_change: Change, anchors: tuple[SettingsOption, ...]) -> None:
        self._on_change = on_change
        self.anchor = _dropdown(
            t("settings.application.anchor"),
            "head_locked",
            tuple(item.semantic_id for item in anchors),
            lambda _e: self._emit(),
        )
        self.offset_x = _number(t("settings.application.offset_x"), 0, lambda _e: self._emit())
        self.offset_y = _number(t("settings.application.offset_y"), -0.45, lambda _e: self._emit())
        self.distance = _number(t("settings.application.distance"), 1.1, lambda _e: self._emit())
        self.text_scale = _number(t("settings.application.text_scale"), 1, lambda _e: self._emit())
        self.alpha = _number(
            t("settings.application.background_alpha"), 0.24, lambda _e: self._emit()
        )
        self._title = ft.Text(label)
        super().__init__(
            [
                self._title,
                ft.ResponsiveRow(
                    [
                        self.anchor,
                        self.offset_x,
                        self.offset_y,
                        self.distance,
                        self.text_scale,
                        self.alpha,
                    ]
                ),
            ]
        )

    def refresh_locale(self, label: str) -> None:
        self._title.value = label
        for control, key in (
            (self.anchor, "anchor"),
            (self.offset_x, "offset_x"),
            (self.offset_y, "offset_y"),
            (self.distance, "distance"),
            (self.text_scale, "text_scale"),
            (self.alpha, "background_alpha"),
        ):
            control.label = t(f"settings.application.{key}")
        for option in self.anchor.options:
            option.text = _option(option.key)

    @property
    def value(self) -> OverlayCalibrationValue:
        return OverlayCalibrationValue(
            self.anchor.value or "head_locked",
            float(self.offset_x.value),
            float(self.offset_y.value),
            float(self.distance.value),
            float(self.text_scale.value),
            float(self.alpha.value),
        )

    @value.setter
    def value(self, value: object) -> None:
        if not isinstance(value, OverlayCalibrationValue):
            return
        (
            self.anchor.value,
            self.offset_x.value,
            self.offset_y.value,
            self.distance.value,
            self.text_scale.value,
            self.alpha.value,
        ) = (
            value.anchor,
            str(value.offset_x),
            str(value.offset_y),
            str(value.distance),
            str(value.text_scale),
            str(value.background_alpha),
        )

    def _emit(self) -> None:
        try:
            self._on_change(self.value)
        except (TypeError, ValueError):
            return


class DesktopOverlayEditor(ft.Column):
    def __init__(self, label: str, on_change: Change, sizes: tuple[SettingsOption, ...]) -> None:
        self._on_change = on_change
        self.size = _dropdown(
            t("settings.application.size"),
            "medium",
            tuple(item.semantic_id for item in sizes),
            lambda _e: self._emit(),
        )
        self.x = _number(t("settings.application.position_x"), None, lambda _e: self._emit())
        self.y = _number(t("settings.application.position_y"), None, lambda _e: self._emit())
        self.lock = ft.Switch(
            label=t("settings.application.position_lock"), on_change=self._lock_changed
        )
        self.alpha = _number(
            t("settings.application.background_alpha"), 0.6, lambda _e: self._emit()
        )
        self._title = ft.Text(label)
        super().__init__(
            [self._title, ft.ResponsiveRow([self.size, self.x, self.y, self.lock, self.alpha])]
        )

    def refresh_locale(self, label: str) -> None:
        self._title.value = label
        for control, key in (
            (self.size, "size"),
            (self.x, "position_x"),
            (self.y, "position_y"),
            (self.lock, "position_lock"),
            (self.alpha, "background_alpha"),
        ):
            control.label = t(f"settings.application.{key}")
        for option in self.size.options:
            option.text = _option(option.key)

    @property
    def value(self) -> DesktopOverlayValue:
        return DesktopOverlayValue(
            self.size.value or "medium",
            None if self.x.value in (None, "") else float(self.x.value),
            None if self.y.value in (None, "") else float(self.y.value),
            float(self.alpha.value),
        )

    @value.setter
    def value(self, value: object) -> None:
        if not isinstance(value, DesktopOverlayValue):
            return
        self.size.value, self.x.value, self.y.value, self.alpha.value = (
            value.size_preset,
            "" if value.x is None else str(value.x),
            "" if value.y is None else str(value.y),
            str(value.background_alpha),
        )
        self.lock.value = value.x is not None and value.y is not None

    def _lock_changed(self, _event) -> None:
        if not self.lock.value:
            self.x.value = self.y.value = ""
        self._emit()

    def _emit(self) -> None:
        try:
            self._on_change(self.value)
        except (TypeError, ValueError):
            return


class LocalExtraBodyEditor(ft.Column):
    def __init__(self, label: str, on_change: Change) -> None:
        self._on_change = on_change
        self._entries: list[JsonScalarEntry] = []
        self._rows = ft.Column()
        self._title = ft.Text(label)
        self._add = ft.OutlinedButton(
            text=t("settings.application.add"), on_click=lambda _e: self._append()
        )
        super().__init__(
            [
                self._title,
                self._rows,
                self._add,
            ]
        )

    def refresh_locale(self, label: str) -> None:
        self._title.value = label
        self._add.text = t("settings.application.add")
        self._render()

    @property
    def value(self) -> LocalExtraBodyValue:
        return LocalExtraBodyValue(tuple(self._entries))

    @value.setter
    def value(self, value: object) -> None:
        if isinstance(value, LocalExtraBodyValue):
            self._entries = list(value.entries)
        else:
            self._entries = [item for item in (value or ()) if isinstance(item, JsonScalarEntry)]
        self._render()

    def _append(self) -> None:
        self._entries.append(JsonScalarEntry("", None))
        self._render()
        self._on_change(self.value)

    @staticmethod
    def _scalar(kind: str, raw: str) -> str | int | float | bool | None:
        if kind == "null":
            return None
        if kind == "boolean":
            return raw.lower() == "true"
        if kind == "integer":
            return int(raw)
        if kind == "number":
            return float(raw)
        return raw

    def _change(self, index: int, key: str | None, kind: str, raw: str) -> None:
        try:
            self._entries[index] = JsonScalarEntry(key or "", self._scalar(kind, raw))
        except ValueError:
            return
        self._on_change(self.value)

    def _remove(self, index: int) -> None:
        self._entries.pop(index)
        self._render()
        self._on_change(self.value)

    def _render(self) -> None:
        rows = []
        for index, entry in enumerate(self._entries):
            kind = (
                "null"
                if entry.value is None
                else (
                    "boolean"
                    if type(entry.value) is bool
                    else (
                        "integer"
                        if type(entry.value) is int
                        else "number" if type(entry.value) is float else "string"
                    )
                )
            )
            key = _text(
                t("settings.application.key"),
                entry.key,
                lambda e, i=index, k=kind, v=str(entry.value or ""): self._change(
                    i, e.control.value, k, v
                ),
            )
            type_control = _dropdown(
                t("settings.application.type"),
                kind,
                ("string", "integer", "number", "boolean", "null"),
                lambda e, i=index, k=entry.key, v=str(entry.value or ""): self._change(
                    i, k, e.control.value or "string", v
                ),
            )
            scalar = _text(
                t("settings.application.value"),
                "" if entry.value is None else entry.value,
                lambda e, i=index, k=entry.key, kind=kind: self._change(
                    i, k, kind, e.control.value or ""
                ),
            )
            rows.append(
                ft.Row(
                    [
                        ft.Icon(ft.Icons.DRAG_HANDLE),
                        key,
                        type_control,
                        scalar,
                        ft.IconButton(
                            ft.Icons.DELETE, on_click=lambda _e, i=index: self._remove(i)
                        ),
                    ]
                )
            )
        self._rows.controls = rows
        _refresh(self._rows)
