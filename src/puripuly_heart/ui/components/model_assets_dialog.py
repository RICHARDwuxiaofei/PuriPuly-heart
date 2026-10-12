from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft

from puripuly_heart.core.feature_diagnostics import log_feature
from puripuly_heart.core.local_translation.assets import e4b_gemma_spec
from puripuly_heart.core.local_translation.provisioning import GemmaProvisioningCancelled
from puripuly_heart.ui.i18n import t


class ModelAssetsDialog:
    def __init__(self, page, application, run_task):
        self.page = page
        self.application = application
        self.run_task = run_task
        self.busy = False
        self.closed = True
        self.files = ()
        self.cancel_requested = False
        self.source = ft.Dropdown(
            value=application.gemma_download_source(),
            options=[
                ft.DropdownOption("official", "Hugging Face"),
                ft.DropdownOption("mirror", "hf-mirror.com"),
                ft.DropdownOption("local", t("model_assets.local")),
            ],
            label=t("model_assets.source"),
        )
        self.status = ft.Text(t("model_assets.hint"), selectable=True)
        self.progress = ft.ProgressBar(value=0)
        self.start_button = ft.Button(
            t("model_assets.start"), on_click=lambda _: self.run_task(self.install)
        )
        self.picker = ft.FilePicker()
        self.dialog = ft.AlertDialog(
            title=ft.Text(t("model_assets.title")),
            content=ft.Column(
                [
                    self.source,
                    ft.Text(
                        "\n".join(a.filename for a in e4b_gemma_spec().assets), selectable=True
                    ),
                    ft.Button(
                        t("model_assets.choose"), on_click=lambda _: self.run_task(self.choose)
                    ),
                    ft.Button(
                        t("model_assets.choose_directory"),
                        on_click=lambda _: self.run_task(self.choose_directory),
                    ),
                    self.status,
                    self.progress,
                ],
                width=520,
                tight=True,
            ),
            actions=[
                self.start_button,
                ft.TextButton(
                    t("model_assets.cancel"), on_click=lambda _: self.run_task(self.cancel)
                ),
                ft.TextButton(t("model_assets.close"), on_click=lambda _: self.close()),
            ],
            on_dismiss=lambda _: self.close(pop=False),
        )

    def open(self):
        if not self.closed:
            return
        self.closed = False
        self.page.show_dialog(self.dialog)

    def close(self, *, pop=True):
        if self.closed:
            return
        self.closed = True
        if self.busy:
            self.run_task(self.cancel)
        if pop:
            self.page.pop_dialog()

    async def cancel(self):
        self.cancel_requested = True
        await self.application.handle_managed_gemma_notice_action("cancel")

    async def choose(self):
        if self.busy:
            return
        try:
            selected = await self.picker.pick_files(
                allow_multiple=True,
                allowed_extensions=["gguf"],
                file_type=ft.FilePickerFileType.CUSTOM,
            )
            if selected:
                self.files = tuple(Path(file.path) for file in selected if file.path)
                self.source.value = "local"
                self.status.value = "\n".join(path.name for path in self.files)
                self.page.update()
        except Exception as error:
            log_feature("ModelDownload", "preflight", "file_picker_failed", error=error)
            self.status.value = t("model_assets.error.unknown")
            self.page.update()

    async def choose_directory(self):
        if self.busy:
            return
        try:
            selected = await self.picker.get_directory_path()
            if selected:
                self.files = tuple(
                    Path(selected) / asset.filename for asset in e4b_gemma_spec().assets
                )
                self.source.value = "local"
                self.status.value = "\n".join(path.name for path in self.files)
                self.page.update()
        except Exception as error:
            log_feature("ModelDownload", "preflight", "file_picker_failed", error=error)
            self.status.value = t("model_assets.error.unknown")
            self.page.update()

    async def install(self):
        if self.busy or self.closed:
            return
        self.busy = True
        self.cancel_requested = False
        source = self.source.value
        self.start_button.disabled = True
        self.source.disabled = True
        self.status.value = t("model_assets.connecting")
        self.page.update()

        def update(status):
            if self.closed:
                return
            self.progress.value = (
                status.downloaded_bytes / status.total_bytes if status.total_bytes else 0
            )
            self.status.value = f"{t('model_assets.phase.' + status.state)} · {source}\n{status.downloaded_bytes / 1024**2:.1f} / {status.total_bytes / 1024**2:.1f} MiB ({status.percent}%)"
            self.page.update()

        try:
            await self.application.configure_gemma_source(source)
            if self.closed or self.cancel_requested:
                raise GemmaProvisioningCancelled("Model task cancelled")
            await self.application.provision_gemma_assets(
                source=source, files=self.files, on_status=update
            )
            self.status.value = t("model_assets.complete")
        except GemmaProvisioningCancelled, asyncio.CancelledError:
            self.status.value = t("model_assets.cancelled")
        except Exception as error:
            code = getattr(error, "failure_code", "unknown")
            log_feature("ModelDownload", "installation", code, error=error)
            self.status.value = t(
                "model_assets.error." + code, default=t("model_assets.error.unknown")
            )
        finally:
            self.busy = False
            self.start_button.disabled = False
            self.source.disabled = False
            if not self.closed:
                self.page.update()
