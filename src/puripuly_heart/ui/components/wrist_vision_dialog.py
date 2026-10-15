from __future__ import annotations

import flet as ft

from puripuly_heart.core.feature_diagnostics import log_feature
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import COLOR_NEUTRAL_DARK


def wrist_labels():
    return dict(
        title=t("wrist.title"),
        buttons=[t("wrist.capture"), t("wrist.older"), t("wrist.newer"), t("wrist.back")],
        processing=t("wrist.processing"),
        result=t("wrist.result"),
        error=t("wrist.error"),
        translation_failed=t("wrist.translation_failed"),
        spatial_fallback=t("wrist.spatial_fallback"),
    )


class WristVisionDialog:
    def __init__(self, page, application, owner, run_task):
        self.page, self.application, self.owner, self.run_task = page, application, owner, run_task
        self.dialog = None

    def open(self):
        self.values = self.application.wrist_vision_preferences()["config"]

        def choice(key, options):
            field = ft.Dropdown(
                label=t("wrist." + key),
                value=self.values[key],
                options=[ft.dropdown.Option(value, label) for value, label in options],
                text_style=ft.TextStyle(color=COLOR_NEUTRAL_DARK),
            )
            field.on_select = lambda e: self.values.__setitem__(key, field.value)
            return field

        self.enabled = ft.Switch(
            label=t("wrist.enabled"),
            value=self.values["enabled"],
            on_change=lambda e: self.values.__setitem__("enabled", self.enabled.value),
        )

        def number(key):
            field = ft.TextField(
                label=t("wrist." + key), value=str(self.values[key]), color=COLOR_NEUTRAL_DARK
            )
            field.on_change = lambda e: self.values.__setitem__(key, field.value)
            return field

        display = [
            ("both", t("wrist.both")),
            ("original", t("wrist.original")),
            ("translation", t("wrist.translation")),
        ]
        self.status = ft.Text(t("wrist.privacy"), color=COLOR_NEUTRAL_DARK)
        spatial = ft.Switch(
            label=t("wrist.spatial_enabled"),
            value=self.values["spatial_enabled"],
            on_change=lambda e: self.values.__setitem__("spatial_enabled", spatial.value),
        )
        fallback = ft.Switch(
            label=t("wrist.fallback_to_wrist"),
            value=self.values["fallback_to_wrist"],
            on_change=lambda e: self.values.__setitem__("fallback_to_wrist", fallback.value),
        )
        language = ft.TextField(
            label=t("wrist.target_language"),
            value=self.values["target_language"],
            color=COLOR_NEUTRAL_DARK,
            on_change=lambda e: self.values.__setitem__("target_language", language.value),
        )
        self.save = ft.Button(t("wrist.apply"), on_click=lambda _: self.run_task(self.apply))
        self.dialog = ft.AlertDialog(
            title=ft.Text(t("wrist.title"), color=COLOR_NEUTRAL_DARK),
            content=ft.Container(
                width=620,
                content=ft.Column(
                    [
                        self.enabled,
                        self.status,
                        choice("hand", [("left", t("wrist.left")), ("right", t("wrist.right"))]),
                        ft.Row([number("width_m"), number("opacity")]),
                        ft.Row([number("offset_x"), number("offset_y"), number("offset_z")]),
                        choice("self_display", display),
                        choice("peer_display", display),
                        language,
                        choice(
                            "model",
                            [
                                ("deepseek-flash", "deepseek-flash"),
                                ("deepseek-v4-flash-vision-exp", t("wrist.legacy_model")),
                            ],
                        ),
                        choice(
                            "capture_mode",
                            [
                                ("wrist_frame", t("wrist.wrist_frame")),
                                ("head_frame", t("wrist.head_frame")),
                            ],
                        ),
                        number("timeout"),
                        spatial,
                        fallback,
                        ft.Text(t("wrist.spatial_fallback"), color=COLOR_NEUTRAL_DARK),
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    tight=True,
                ),
            ),
            actions=[
                self.save,
                ft.TextButton(
                    t("wrist.cancel_task"), on_click=lambda _: self.run_task(self.owner.cancel)
                ),
                ft.TextButton(t("wrist.close"), on_click=lambda _: self.page.pop_dialog()),
            ],
        )
        self.page.show_dialog(self.dialog)

    async def apply(self):
        self.save.disabled = True
        self.page.update()
        try:
            values = dict(self.values)
            for key in ("width_m", "opacity", "offset_x", "offset_y", "offset_z", "timeout"):
                values[key] = float(values[key])
            await self.application.configure_wrist_vision(values)
            if values["enabled"]:
                result = await self.owner.enable()
                if not result:
                    self.status.value = t("wrist.error") + " (" + str(self.owner.code) + ")"
                else:
                    self.status.value = t("wrist.ready")
            else:
                await self.owner.disable()
                self.status.value = t("wrist.off")
        except Exception as error:
            log_feature("WristVision", "settings", "settings_apply_failed", error=error)
            self.status.value = t("wrist.settings_failed")
        finally:
            self.save.disabled = False
            self.page.update()
