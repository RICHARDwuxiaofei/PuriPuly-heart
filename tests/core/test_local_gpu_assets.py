from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import puripuly_heart.core.local_gpu_assets as local_gpu_assets_module
from puripuly_heart.config.gpu_model_catalog import (
    LOCAL_QWEN_GPU_06_MODEL_ID,
    LOCAL_QWEN_GPU_17_MODEL_ID,
    require_local_gpu_model,
)
from puripuly_heart.core.local_gpu_assets import (
    LOCAL_QWEN_GPU_ENGINE,
    LOCAL_QWEN_GPU_MODEL_FILENAME,
    inspect_local_gpu_install,
    load_local_gpu_asset_manifest,
    local_gpu_model_path,
)
from puripuly_heart.core.local_stt_assets import (
    LOCAL_QWEN_GPU_MODEL_ID,
    LocalSTTAssetFile,
    LocalSTTAssetManifest,
    LocalSTTAssetSource,
)


def _fixture_manifest(
    payload: bytes,
    *,
    model_id: str = LOCAL_QWEN_GPU_MODEL_ID,
) -> LocalSTTAssetManifest:
    entry = require_local_gpu_model(model_id)
    return LocalSTTAssetManifest(
        manifest_version=1,
        installed_manifest_version=1,
        model_id=model_id,
        engine=LOCAL_QWEN_GPU_ENGINE,
        upstream_repo="fixture@revision",
        install_dirname=entry.install_dirname,
        sources={
            "fixture": LocalSTTAssetSource(
                name="fixture",
                revision="revision",
                repo_id="fixture/repo",
                download_url_template="https://example.invalid/{relative_path}",
            )
        },
        files=(
            LocalSTTAssetFile(
                relative_path=entry.gguf_filename,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            ),
        ),
    )


def _write_installed_contract(root: Path, manifest: LocalSTTAssetManifest) -> Path:
    install_root = root / manifest.install_dirname
    install_root.mkdir(parents=True)
    installed = {
        "manifest_version": manifest.installed_manifest_version,
        "model_id": manifest.model_id,
        "engine": manifest.engine,
        "install_dirname": manifest.install_dirname,
        "selected_source": "fixture",
        "selected_revision": "revision",
    }
    (install_root / manifest.installed_manifest_filename).write_text(
        json.dumps(installed),
        encoding="utf-8",
    )
    return install_root


@pytest.mark.parametrize(
    (
        "model_id",
        "upstream_repo",
        "source_repo",
        "source_revision",
        "filename",
        "size_bytes",
        "sha256",
    ),
    (
        (
            LOCAL_QWEN_GPU_06_MODEL_ID,
            "Qwen/Qwen3-ASR-0.6B@5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
            "handy-computer/Qwen3-ASR-0.6B-gguf",
            "e4e16599b900eb0cb36e524514756bb92eb092b7",
            "Qwen3-ASR-0.6B-Q6_K.gguf",
            690_417_824,
            "3b051f108f03c0c91bbe1a3b2c1ee15e3ed51e4caec2a48751b01f2a21441cc3",
        ),
        (
            LOCAL_QWEN_GPU_17_MODEL_ID,
            "Qwen/Qwen3-ASR-1.7B@7278e1e70fe206f11671096ffdd38061171dd6e5",
            "handy-computer/Qwen3-ASR-1.7B-gguf",
            "92282af1610a2db19d66f2bef1e260f5deca782d",
            "Qwen3-ASR-1.7B-Q6_K.gguf",
            1_692_554_208,
            "c75a961b7134a6c952d89797865cb0d0376876185aee04ef6d12c31c2952e4e1",
        ),
    ),
)
def test_gpu_manifest_pins_one_strict_vulkan_q6_k_asset(
    model_id: str,
    upstream_repo: str,
    source_repo: str,
    source_revision: str,
    filename: str,
    size_bytes: int,
    sha256: str,
) -> None:
    manifest = load_local_gpu_asset_manifest(model_id)

    assert manifest.model_id == model_id
    assert manifest.engine == LOCAL_QWEN_GPU_ENGINE
    assert manifest.upstream_repo == upstream_repo
    assert manifest.install_dirname == require_local_gpu_model(model_id).install_dirname
    assert manifest.sources["huggingface"].repo_id == source_repo
    assert manifest.sources["huggingface"].revision == source_revision
    assert [(item.relative_path, item.size_bytes, item.sha256) for item in manifest.files] == [
        (filename, size_bytes, sha256)
    ]
    source = manifest.sources["huggingface"]
    assert source.download_url_template.format(path=manifest.files[0].relative_path) == (
        f"https://huggingface.co/{source_repo}/resolve/{source_revision}/{filename}"
    )


def test_gpu_models_have_independent_manifests_install_roots_and_hashes() -> None:
    manifests = {
        model_id: load_local_gpu_asset_manifest(model_id)
        for model_id in (LOCAL_QWEN_GPU_06_MODEL_ID, LOCAL_QWEN_GPU_17_MODEL_ID)
    }

    assert {manifest.model_id for manifest in manifests.values()} == set(manifests)
    assert len({manifest.install_dirname for manifest in manifests.values()}) == 2
    assert len({manifest.files[0].relative_path for manifest in manifests.values()}) == 2
    assert len({manifest.files[0].sha256 for manifest in manifests.values()}) == 2
    assert all(
        manifest.install_dirname == require_local_gpu_model(model_id).install_dirname
        for model_id, manifest in manifests.items()
    )


def test_gpu_manifest_rejects_catalog_filename_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_local_gpu_asset_manifest(LOCAL_QWEN_GPU_06_MODEL_ID)
    mismatched = replace(
        manifest,
        files=(replace(manifest.files[0], relative_path="unexpected.gguf"),),
    )
    monkeypatch.setattr(
        local_gpu_assets_module,
        "load_local_stt_asset_manifest",
        lambda _model_id: mismatched,
    )

    with pytest.raises(ValueError, match="file does not match catalog"):
        load_local_gpu_asset_manifest(LOCAL_QWEN_GPU_06_MODEL_ID)


def test_valid_gpu_model_install_does_not_validate_the_other_gpu_model(tmp_path: Path) -> None:
    first_model_id = LOCAL_QWEN_GPU_06_MODEL_ID
    second_model_id = LOCAL_QWEN_GPU_17_MODEL_ID
    first_entry = require_local_gpu_model(first_model_id)
    payload = b"gpu-model-fixture"
    first_manifest = _fixture_manifest(payload, model_id=first_model_id)
    second_manifest = _fixture_manifest(payload, model_id=second_model_id)
    install_root = _write_installed_contract(tmp_path, first_manifest)
    (install_root / first_entry.gguf_filename).write_bytes(payload)

    first = inspect_local_gpu_install(
        explicit_opt_in=True,
        model_id=first_model_id,
        model_root=tmp_path,
        manifest=first_manifest,
    )
    second = inspect_local_gpu_install(
        explicit_opt_in=True,
        model_id=second_model_id,
        model_root=tmp_path,
        manifest=second_manifest,
    )

    assert first.activation_allowed
    assert second.status == "missing"
    assert not second.activation_allowed


def test_gpu_install_is_not_inspected_before_explicit_opt_in(tmp_path: Path) -> None:
    snapshot = inspect_local_gpu_install(explicit_opt_in=False, model_root=tmp_path)

    assert snapshot.status == "not_requested"
    assert not snapshot.activation_allowed
    assert snapshot.state is None
    assert list(tmp_path.iterdir()) == []


def test_gpu_install_requires_its_own_exact_asset_contract(tmp_path: Path) -> None:
    payload = b"gpu-model-fixture"
    manifest = _fixture_manifest(payload)

    missing = inspect_local_gpu_install(
        explicit_opt_in=True,
        model_root=tmp_path,
        manifest=manifest,
    )
    assert missing.status == "missing"
    assert not missing.activation_allowed

    install_root = _write_installed_contract(tmp_path, manifest)
    model_path = install_root / LOCAL_QWEN_GPU_MODEL_FILENAME
    model_path.write_bytes(b"wrong")
    invalid = inspect_local_gpu_install(
        explicit_opt_in=True,
        model_root=tmp_path,
        manifest=manifest,
    )
    assert invalid.status == "invalid"
    assert not invalid.activation_allowed

    model_path.write_bytes(payload)
    ready = inspect_local_gpu_install(
        explicit_opt_in=True,
        model_root=tmp_path,
        manifest=manifest,
    )
    assert ready.status == "ready"
    assert ready.activation_allowed
    assert local_gpu_model_path(tmp_path, manifest=manifest) == model_path
