from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from puripuly_heart.config.gpu_model_catalog import (
    DEFAULT_LOCAL_QWEN_GPU_MODEL_ID,
    LOCAL_QWEN_GPU_MODEL_ID,
    require_local_gpu_model,
)
from puripuly_heart.core.local_stt_assets import (
    LocalSTTAssetManifest,
    LocalSTTInstallState,
    default_local_stt_model_root,
    inspect_local_stt_install_state,
    load_local_stt_asset_manifest,
)

LOCAL_QWEN_GPU_ENGINE = require_local_gpu_model(LOCAL_QWEN_GPU_MODEL_ID).engine
LOCAL_QWEN_GPU_MODEL_FILENAME = require_local_gpu_model(LOCAL_QWEN_GPU_MODEL_ID).gguf_filename
LocalGPUOptInStatus = Literal["not_requested", "missing", "invalid", "ready"]


@dataclass(frozen=True, slots=True)
class LocalGPUInstallSnapshot:
    explicit_opt_in: bool
    status: LocalGPUOptInStatus
    model_id: str = LOCAL_QWEN_GPU_MODEL_ID
    state: LocalSTTInstallState | None = None

    @property
    def activation_allowed(self) -> bool:
        return (
            self.explicit_opt_in
            and self.status == "ready"
            and self.state is not None
            and self.state.installed_manifest is not None
            and self.state.installed_manifest.model_id == self.model_id
        )


def load_local_gpu_asset_manifest(
    model_id: str = DEFAULT_LOCAL_QWEN_GPU_MODEL_ID,
) -> LocalSTTAssetManifest:
    entry = require_local_gpu_model(model_id)
    manifest = load_local_stt_asset_manifest(entry.model_id)
    if manifest.engine != entry.engine:
        raise ValueError("local GPU model manifest engine is not strict Vulkan transcribe.cpp")
    if manifest.install_dirname != entry.install_dirname:
        raise ValueError("local GPU model manifest install directory does not match catalog")
    if tuple(item.relative_path for item in manifest.files) != (entry.gguf_filename,):
        raise ValueError("local GPU model manifest file does not match catalog")
    return manifest


def inspect_local_gpu_install(
    *,
    explicit_opt_in: bool,
    model_id: str = DEFAULT_LOCAL_QWEN_GPU_MODEL_ID,
    model_root: Path | None = None,
    verify_checksums: bool = True,
    manifest: LocalSTTAssetManifest | None = None,
) -> LocalGPUInstallSnapshot:
    entry = require_local_gpu_model(model_id)
    if not explicit_opt_in:
        return LocalGPUInstallSnapshot(
            explicit_opt_in=False,
            status="not_requested",
            model_id=entry.model_id,
        )
    resolved_manifest = manifest or load_local_gpu_asset_manifest(entry.model_id)
    if resolved_manifest.model_id != entry.model_id:
        raise ValueError("local GPU install manifest does not match selected model")
    root = model_root or default_local_stt_model_root()
    state = inspect_local_stt_install_state(
        root / resolved_manifest.install_dirname,
        manifest=resolved_manifest,
        verify_checksums=verify_checksums,
    )
    return LocalGPUInstallSnapshot(
        explicit_opt_in=True,
        status=state.status,
        model_id=entry.model_id,
        state=state,
    )


def local_gpu_model_path(
    model_root: Path | None = None,
    *,
    model_id: str = DEFAULT_LOCAL_QWEN_GPU_MODEL_ID,
    manifest: LocalSTTAssetManifest | None = None,
) -> Path:
    entry = require_local_gpu_model(model_id)
    resolved_manifest = manifest or load_local_gpu_asset_manifest(entry.model_id)
    if resolved_manifest.model_id != entry.model_id:
        raise ValueError("local GPU model manifest does not match selected model")
    root = model_root or default_local_stt_model_root()
    matching = [
        item for item in resolved_manifest.files if item.relative_path == entry.gguf_filename
    ]
    if len(matching) != 1:
        raise ValueError("local GPU model manifest must contain exactly one catalog model file")
    return root / resolved_manifest.install_dirname / matching[0].relative_path
