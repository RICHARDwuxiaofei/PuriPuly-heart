from __future__ import annotations

import re
import tomllib
from pathlib import Path

from puripuly_heart.core.local_qwen_runtime import LOCAL_QWEN_PACKAGED_RUNTIME_RELATIVE_DIR

ROOT = Path(__file__).resolve().parents[2]
PINNED_PYTHON_VERSION = 'PYTHON_VERSION: "3.12.10"'
PINNED_UV_VERSION = 'UV_VERSION: "0.9.17"'
PINNED_INNOSETUP_VERSION = 'INNOSETUP_VERSION: "6.6.1"'
SHARED_SETUP_ACTION = "./.github/actions/setup-uv-environment"
PINNED_SOXR_SPECIFIER = "soxr==1.0.0"
SOXR_RELEASE_INPUTS_SCRIPT = "scripts/ci/prepare-soxr-release-inputs.ps1"
README_SOXR_RELEASE_PREPARE_COMMAND = r".\scripts\ci\prepare-soxr-release-inputs.ps1"
README_PYINSTALLER_BUILD_COMMAND = r".venv\Scripts\pyinstaller build.spec"
SOXR_RELEASE_INPUTS_MANIFEST_PATH = "build/soxr-release-inputs/manifest.json"
SOXR_PACKAGED_RUNTIME_RELATIVE_DIR = "soxr"
SOXR_COMPLIANCE_BUNDLE_RELATIVE_DIR = "third_party\\soxr"
SOXR_SOURCE_BUNDLE_NAME = "PuriPulyHeart-soxr-third-party-source-bundle.zip"
SOXR_LICENSE_TEXT_RELATIVE_PATH = "src/puripuly_heart/data/licenses/COPYING.LGPL-2.1.txt"
PINNED_LIBSOXR_SOURCE_URL = (
    "https://sourceforge.net/projects/soxr/files/soxr-0.1.3-Source.tar.xz/download"
)
PINNED_LIBSOXR_SOURCE_SHA256 = "b111c15fdc8c029989330ff559184198c161100a59312f5dc19ddeb9b5a15889"
README_INSTALLER_BUILD_COMMAND = "ISCC installer.iss"
README_FULL_RELEASE_COMMAND = r".\scripts\ci\build-release-artifacts.ps1"
FULL_WINDOWS_RELEASE_SCRIPT = "scripts/ci/build-release-artifacts.ps1"
README_BUILD_SECTION_MARKERS = {
    "README.md": (
        "Direct executable-only / manual packaging steps:",
        "For the release-complete compliance-packaging path, run `scripts/ci/prepare-soxr-release-inputs.ps1` first and then `scripts/ci/build-release-artifacts.ps1`:",
    ),
    "README.ko.md": (
        "실행 파일 전용 / 수동 패키징 단계:",
        "릴리스 완료(compliance) Windows 패키징 경로는 먼저 `scripts/ci/prepare-soxr-release-inputs.ps1`를 실행한 뒤 `scripts/ci/build-release-artifacts.ps1`를 실행합니다:",
    ),
    "README.ja.md": (
        "実行ファイル単体 / 手動パッケージング手順:",
        "リリース完了のコンプライアンスパッケージングでは、先に `scripts/ci/prepare-soxr-release-inputs.ps1` を実行し、その後 `scripts/ci/build-release-artifacts.ps1` を実行します:",
    ),
    "README.zh-CN.md": (
        "仅生成可执行文件 / 手动打包步骤：",
        "完整发布所需的 Windows 合规打包路径需要先运行 `scripts/ci/prepare-soxr-release-inputs.ps1`，再运行 `scripts/ci/build-release-artifacts.ps1`：",
    ),
}
README_DIRECT_PACKAGING_CAVEATS = {
    "README.md": (
        "This direct executable/manual-installer path is not the release-complete "
        "compliance-packaging path. It also requires the staged overlay executable at "
        "`build/overlay/PuriPulyHeartOverlay.exe`, as enforced by `build.spec`."
    ),
    "README.ko.md": (
        "이 경로는 실행 파일/수동 인스톨러만 만드는 직접 패키징 경로이며, "
        "릴리스 완료(compliance) 패키징 경로가 아닙니다. 또한 `build.spec`가 "
        "검사하는 스테이징된 오버레이 실행 파일 `build/overlay/PuriPulyHeartOverlay.exe`가 "
        "필요합니다."
    ),
    "README.ja.md": (
        "この手順は実行ファイル/手動インストーラ向けの直接パッケージング専用で、"
        "リリース完了のコンプライアンスパッケージング手順ではありません。さらに、"
        "`build.spec` が検証するステージ済みオーバーレイ実行ファイル "
        "`build/overlay/PuriPulyHeartOverlay.exe` が必要です。"
    ),
    "README.zh-CN.md": (
        "这一路径只用于直接生成可执行文件 / 手动安装包，不是完整发布所需的合规打包路径。"
        "并且它仍然需要 `build.spec` 会校验的已暂存 overlay 可执行文件 "
        "`build/overlay/PuriPulyHeartOverlay.exe`。"
    ),
}
BUILD_SPEC_DIRECT_PACKAGING_HEADER = (
    "Direct Windows PyInstaller packaging (executable-only / manual installer packaging):"
)
BUILD_SPEC_DIRECT_PACKAGING_CAVEAT = (
    "This direct path is not the release-complete compliance-packaging path and requires "
    "the staged overlay executable at build/overlay/PuriPulyHeartOverlay.exe (enforced below)."
)
BUILD_SPEC_FULL_RELEASE_HEADER = "Full release-complete compliance packaging requires scripts/ci/prepare-soxr-release-inputs.ps1 before scripts/ci/build-release-artifacts.ps1:"


def _slice_section(text: str, start_marker: str, end_marker: str | None = None) -> str:
    start_index = text.index(start_marker)

    if end_marker is None:
        return text[start_index:]

    end_index = text.index(end_marker, start_index)
    return text[start_index:end_index]


def _workflow_job_block(workflow: str, job_name: str) -> str:
    lines = workflow.splitlines()
    start_index = lines.index(f"  {job_name}:")
    end_index = len(lines)

    for index in range(start_index + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end_index = index
            break

    return "\n".join(lines[start_index:end_index])


def test_pyproject_caps_deepgram_sdk_below_v6() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "deepgram-sdk>=5.0.0,<6.0.0" in pyproject["project"]["dependencies"]


def test_pyproject_includes_sherpa_onnx_dependency() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "sherpa-onnx>=1.12.36" in pyproject["project"]["dependencies"]


def test_pyproject_pins_soxr_dependency() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert PINNED_SOXR_SPECIFIER in pyproject["project"]["dependencies"]


def test_pyproject_build_extra_covers_python_soxr_no_build_isolation_backend() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    build_extra = pyproject["project"]["optional-dependencies"]["build"]

    assert "scikit-build-core>=0.10" in build_extra
    assert "nanobind>=2" in build_extra
    assert "setuptools_scm[toml]>=6.2" in build_extra


def test_uv_lock_pins_sherpa_onnx_version() -> None:
    uv_lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    match = re.search(
        r'\[\[package\]\]\s+name = "sherpa-onnx"\s+version = "([^"]+)"',
        uv_lock,
        re.MULTILINE,
    )

    assert match is not None
    assert match.group(1) == "1.12.36"


def test_uv_lock_pins_soxr_version() -> None:
    uv_lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    match = re.search(
        r'\[\[package\]\]\s+name = "soxr"\s+version = "([^"]+)"',
        uv_lock,
        re.MULTILINE,
    )

    assert match is not None
    assert match.group(1) == "1.0.0"


def test_uv_lock_includes_python_soxr_build_backend_packages() -> None:
    uv_lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    assert 'name = "scikit-build-core"' in uv_lock
    assert 'name = "nanobind"' in uv_lock
    assert 'name = "setuptools-scm"' in uv_lock


def test_release_workflow_uses_frozen_lockfile_sync() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert SHARED_SETUP_ACTION in workflow
    assert 'python -m pip install -e ".[build]"' not in workflow


def test_push_ci_workflow_uses_frozen_lockfile_sync() -> None:
    workflow = (ROOT / ".github" / "workflows" / "push-ci.yml").read_text(encoding="utf-8")

    assert SHARED_SETUP_ACTION in workflow
    assert 'python -m pip install -e ".[dev]"' not in workflow


def test_workflows_pin_exact_python_and_uv_versions() -> None:
    for workflow_path in (
        ROOT / ".github" / "workflows" / "push-ci.yml",
        ROOT / ".github" / "workflows" / "release.yml",
    ):
        workflow = workflow_path.read_text(encoding="utf-8")
        assert PINNED_PYTHON_VERSION in workflow
        assert PINNED_UV_VERSION in workflow
        assert SHARED_SETUP_ACTION in workflow


def test_workflows_pin_innosetup_and_use_shared_windows_build_script() -> None:
    for workflow_path in (
        ROOT / ".github" / "workflows" / "push-ci.yml",
        ROOT / ".github" / "workflows" / "release.yml",
    ):
        workflow = workflow_path.read_text(encoding="utf-8")
        assert PINNED_INNOSETUP_VERSION in workflow
        assert "scripts/ci/build-release-artifacts.ps1" in workflow


def test_readmes_distinguish_direct_packaging_from_full_release_complete_path() -> None:
    for readme_name, (direct_marker, full_release_marker) in README_BUILD_SECTION_MARKERS.items():
        readme = (ROOT / readme_name).read_text(encoding="utf-8")
        direct_section = _slice_section(readme, direct_marker, full_release_marker)
        full_release_section = _slice_section(readme, full_release_marker)

        assert direct_marker in readme
        assert full_release_marker in readme
        assert README_DIRECT_PACKAGING_CAVEATS[readme_name] in direct_section
        assert README_SOXR_RELEASE_PREPARE_COMMAND in direct_section
        assert README_PYINSTALLER_BUILD_COMMAND in direct_section
        assert README_INSTALLER_BUILD_COMMAND in direct_section
        assert direct_section.index(README_SOXR_RELEASE_PREPARE_COMMAND) < direct_section.index(
            README_PYINSTALLER_BUILD_COMMAND
        )
        assert direct_section.index(README_PYINSTALLER_BUILD_COMMAND) < direct_section.index(
            README_INSTALLER_BUILD_COMMAND
        )
        assert README_SOXR_RELEASE_PREPARE_COMMAND in full_release_section
        assert README_FULL_RELEASE_COMMAND in full_release_section
        assert full_release_section.index(
            README_SOXR_RELEASE_PREPARE_COMMAND
        ) < full_release_section.index(README_FULL_RELEASE_COMMAND)


def test_push_ci_has_windows_release_path_job() -> None:
    workflow = (ROOT / ".github" / "workflows" / "push-ci.yml").read_text(encoding="utf-8")

    assert "runs-on: windows-latest" in workflow
    assert "Build Windows release path" in workflow


def test_shared_windows_build_script_runs_packaged_smoke_test() -> None:
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert "Start-Process" in script
    assert '"--clean"' in script
    assert '"--version"' in script
    assert "_multiarray_umath" in script
    assert 'Get-ChildItem -Path $distDir -Filter "_multiarray_umath*.pyd" -Recurse' in script
    assert "osc-send" in script
    assert "Remove-Item -Recurse -Force $pyInstallerBuildDir" in script
    assert "Remove-Item -Recurse -Force $distDir" in script
    assert '"innosetup"' in script
    assert '"--version=$InnoSetupVersion"' in script
    assert "DisplayVersion" in script
    assert "Get-Command choco" in script


def test_shared_windows_build_script_uses_alternate_app_id_and_isolated_installer_smoke_dir() -> (
    None
):
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert "$InstallerTestAppId" in script
    assert (
        '$InstallerSmokeDir = Join-Path $env:LOCALAPPDATA "Programs\\PuriPulyHeart-LocalSTT-Test"'
        in script
    )
    assert "$InstallerSmokeDir" in script
    assert '"/CURRENTUSER"' in script
    assert '"/VERYSILENT"' in script
    assert '"/SUPPRESSMSGBOXES"' in script
    assert '"/DIR=$InstallerSmokeDir"' in script


def test_shared_windows_build_script_builds_release_installer_without_alternate_app_id() -> None:
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert 'Invoke-ExternalProcess -FilePath $isccPath -ArgumentList @("installer.iss")' in script


def test_shared_windows_build_script_uses_separate_smoke_installer_build_with_alternate_app_id() -> (
    None
):
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert "$InstallerSmokeBuildDir" in script
    assert "$InstallerSmokeAppDataRoot" in script
    assert '"/DMyAppId=$InstallerTestAppId"' in script
    assert '"/O$InstallerSmokeBuildDir"' in script
    assert "$smokeInstallerPath" in script


def test_shared_windows_build_script_overrides_local_stt_appdata_for_smoke_and_checks_log() -> None:
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert "PURIPULY_HEART_LOCAL_STT_APPDATA_ROOT" in script
    assert "$InstallerSmokeLogPath" in script
    assert '"/LOG=$InstallerSmokeLogPath"' in script
    assert "Local STT provisioning completed successfully." in script


def test_shared_windows_build_script_bundles_openvr_runtime_dll_for_overlay() -> None:
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert "openvr_api.dll" in script
    assert "SteamVR\\bin\\win64\\openvr_api.dll" in script
    assert '$overlayBundledDllPath = Join-Path $overlayBuildDir "openvr_api.dll"' in script
    assert '$packagedOverlayDllPath = Join-Path $distDir "openvr_api.dll"' in script
    assert (
        "Copy-Item -Path $openVrRuntimeDllPath -Destination $overlayBundledDllPath -Force" in script
    )
    assert (
        "Copy-Item -Path $openVrRuntimeDllPath -Destination $packagedOverlayDllPath -Force"
        in script
    )


def test_build_spec_local_qwen_runtime_dlls() -> None:
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")

    assert LOCAL_QWEN_PACKAGED_RUNTIME_RELATIVE_DIR.as_posix() == "_runtime/local_qwen"
    assert (
        "from puripuly_heart.core.local_qwen_runtime import "
        "LOCAL_QWEN_PACKAGED_RUNTIME_RELATIVE_DIR" in spec
    )
    assert re.search(
        r'collect_dynamic_libs\(\s*"onnxruntime",\s*'
        r"destdir=LOCAL_QWEN_PACKAGED_RUNTIME_RELATIVE_DIR\.as_posix\(\)\s*\)",
        spec,
    )
    assert "binaries=runtime_binaries" in spec


def test_build_spec_header_distinguishes_direct_packaging_from_full_release_complete_path() -> None:
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")
    direct_section = _slice_section(
        spec, BUILD_SPEC_DIRECT_PACKAGING_HEADER, BUILD_SPEC_FULL_RELEASE_HEADER
    )
    full_release_section = _slice_section(spec, BUILD_SPEC_FULL_RELEASE_HEADER)

    assert BUILD_SPEC_DIRECT_PACKAGING_HEADER in spec
    assert BUILD_SPEC_DIRECT_PACKAGING_CAVEAT in direct_section
    assert BUILD_SPEC_FULL_RELEASE_HEADER in spec
    assert FULL_WINDOWS_RELEASE_SCRIPT in spec
    assert SOXR_RELEASE_INPUTS_SCRIPT in direct_section
    assert "pyinstaller build.spec" in direct_section
    assert README_INSTALLER_BUILD_COMMAND in direct_section
    assert direct_section.index(SOXR_RELEASE_INPUTS_SCRIPT) < direct_section.index(
        "pyinstaller build.spec"
    )
    assert direct_section.index("pyinstaller build.spec") < direct_section.index(
        README_INSTALLER_BUILD_COMMAND
    )
    assert SOXR_RELEASE_INPUTS_SCRIPT in full_release_section
    assert FULL_WINDOWS_RELEASE_SCRIPT in full_release_section
    assert full_release_section.index(SOXR_RELEASE_INPUTS_SCRIPT) < full_release_section.index(
        FULL_WINDOWS_RELEASE_SCRIPT
    )


def test_shared_windows_build_script_checks_packaged_local_qwen_runtime_dir_for_onnx_dlls() -> None:
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert '$packagedLocalQwenRuntimeDir = Join-Path $distDir "_runtime\\local_qwen"' in script
    assert (
        '$packagedLocalQwenFallbackRuntimeDir = Join-Path $distDir "_internal\\_runtime\\local_qwen"'
        in script
    )
    assert "if (-not (Test-Path $packagedLocalQwenRuntimeDir)) {" in script
    assert "$packagedLocalQwenRuntimeDir = $packagedLocalQwenFallbackRuntimeDir" in script
    assert (
        '$packagedOnnxRuntimeDllPath = Join-Path $packagedLocalQwenRuntimeDir "onnxruntime.dll"'
        in script
    )
    assert (
        "$packagedOnnxRuntimeProvidersSharedDllPath = Join-Path "
        '$packagedLocalQwenRuntimeDir "onnxruntime_providers_shared.dll"' in script
    )
    assert "if (-not (Test-Path $packagedOnnxRuntimeDllPath)) {" in script
    assert "if (-not (Test-Path $packagedOnnxRuntimeProvidersSharedDllPath)) {" in script


def test_build_spec_numpy_runtime_guard_narrow() -> None:
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")

    assert '"numpy._core._multiarray_umath"' in spec
    assert 'collect_dynamic_libs("numpy")' not in spec
    assert 'collect_submodules("numpy")' not in spec


def test_prepare_soxr_release_inputs_script_builds_system_linked_runtime_and_source_bundle() -> (
    None
):
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert PINNED_SOXR_SPECIFIER in script
    assert PINNED_LIBSOXR_SOURCE_SHA256 in script
    assert "USE_SYSTEM_LIBSOXR=ON" in script
    assert '"soxr.dll"' in script
    assert SOXR_RELEASE_INPUTS_MANIFEST_PATH in script
    assert SOXR_SOURCE_BUNDLE_NAME in script
    assert "Compress-Archive" in script
    assert "--no-build-isolation" in script
    assert '"-DCMAKE_SHARED_LIBRARY_PREFIX=lib"' not in script
    assert '"-DCMAKE_IMPORT_LIBRARY_PREFIX=lib"' not in script


def test_prepare_soxr_release_inputs_script_uses_pinned_libsoxr_hash_and_repo_controlled_wheel_build() -> (
    None
):
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert f'$LibsoxrSourceUrl = "{PINNED_LIBSOXR_SOURCE_URL}"' in script
    assert f'$expectedLibsoxrSourceSha256 = "{PINNED_LIBSOXR_SOURCE_SHA256}"' in script
    assert "$libsoxrSourceSha256 -ne $expectedLibsoxrSourceSha256" in script
    assert "python-soxr source wheel using the prepared project environment" in script
    assert '"--no-build-isolation"' in script


def test_prepare_soxr_release_inputs_script_downloads_libsoxr_with_curl_follow_redirects() -> None:
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert 'Resolve-CommandPath -Name "curl.exe"' in script
    assert "Invoke-External -FilePath $curlCommand -ArgumentList @(" in script
    assert '"-L"' in script
    assert "$LibsoxrSourceUrl" in script
    assert "$libsoxrSourcePath" in script


def test_prepare_soxr_release_inputs_script_sets_cmake_policy_minimum_for_libsoxr_013() -> None:
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert '"-DCMAKE_POLICY_VERSION_MINIMUM=3.5"' in script


def test_prepare_soxr_release_inputs_script_renames_windows_soxr_outputs_to_packaged_soxr_names() -> (
    None
):
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert '$libsoxrBuiltDllPath = Join-Path $libsoxrInstallDir "bin\\soxr.dll"' in script
    assert '$libsoxrImportLibPath = Join-Path $libsoxrInstallDir "lib\\soxr.lib"' in script
    assert '$stagedSoxrDllPath = Join-Path $runtimeStageDir "soxr.dll"' in script
    assert "Copy-Item -Path $libsoxrBuiltDllPath -Destination $stagedSoxrDllPath -Force" in script
    assert '"-DCMAKE_SHARED_LIBRARY_PREFIX=lib"' not in script
    assert '"-DCMAKE_IMPORT_LIBRARY_PREFIX=lib"' not in script


def test_prepare_soxr_release_inputs_script_bootstraps_pip_before_wheel_build() -> None:
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert '"ensurepip"' in script
    assert script.index('"ensurepip"') < script.index('"pip"')


def test_prepare_soxr_release_inputs_script_passes_nanobind_cmake_dir_to_python_soxr_build() -> (
    None
):
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert "$nanobindCmakeDir" in script
    assert "nanobind-config.cmake" in script
    assert '"--config-settings=cmake.define.nanobind_DIR=$nanobindCmakeDir"' in script


def test_prepare_soxr_release_inputs_script_adds_built_libsoxr_bin_to_path_before_wheel_build() -> (
    None
):
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert '$libsoxrBinDir = Join-Path $libsoxrInstallDir "bin"' in script
    assert '$env:PATH = "$libsoxrBinDir;$env:PATH"' in script


def test_prepare_soxr_release_inputs_script_stages_soxr_dll_beside_build_python_for_stubgen() -> (
    None
):
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert '$wheelBuildSoxrDllPath = Join-Path $projectEnvironmentScripts "soxr.dll"' in script
    assert (
        "Copy-Item -Path $libsoxrBuiltDllPath -Destination $wheelBuildSoxrDllPath -Force" in script
    )


def test_prepare_soxr_release_inputs_script_skips_python_soxr_stub_generation_in_release_input_build() -> (
    None
):
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert '$soxrExtractRoot = Join-Path $ReleaseInputsRoot "soxr-src"' in script
    assert '$soxrCMakeListsPath = Join-Path $soxrSourceRoot.FullName "CMakeLists.txt"' in script
    assert "nanobind_add_stub(soxr_ext_stub" in script
    assert "if (NOT CMAKE_CROSSCOMPILING)" in script
    assert "if (FALSE) # release-input wheel build disables stub generation" in script


def test_prepare_soxr_release_inputs_script_uses_uri_relative_path_helper_for_manifest_paths() -> (
    None
):
    script = (ROOT / SOXR_RELEASE_INPUTS_SCRIPT).read_text(encoding="utf-8")

    assert "[System.Uri]::new" in script
    assert "[System.IO.Path]::GetRelativePath" not in script


def test_build_spec_uses_prepared_soxr_release_inputs_and_guards_packaged_layout() -> None:
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")

    assert SOXR_RELEASE_INPUTS_SCRIPT in spec
    assert SOXR_RELEASE_INPUTS_MANIFEST_PATH in spec
    assert 'read_text(encoding="utf-8-sig")' in spec
    assert 'contents_directory="."' in spec
    assert SOXR_PACKAGED_RUNTIME_RELATIVE_DIR in spec
    assert '"soxr"' in spec
    assert '"soxr.soxr_ext"' in spec
    assert "soxr.dll" in spec
    assert 'collect_dynamic_libs("soxr")' not in spec


def test_build_spec_deduplicates_only_root_level_auto_collected_soxr_dll() -> None:
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")

    assert "def normalize_soxr_runtime_binaries(binaries):" in spec
    assert 'normalized_destination_name = destination_name.replace("\\\\", "/")' in spec
    assert 'normalized_destination_name == "soxr.dll"' in spec
    assert "Path(source_path).resolve() == sibling_dll_path" in spec
    assert "binaries[:] = [" in spec
    assert "normalize_soxr_runtime_binaries(a.binaries)" in spec
    assert spec.index("a = Analysis(") < spec.index("normalize_soxr_runtime_binaries(a.binaries)")


def test_push_ci_workflow_prepares_soxr_release_inputs_before_build_release_artifacts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "push-ci.yml").read_text(encoding="utf-8")
    job_block = _workflow_job_block(workflow, "windows-release-path")

    assert SOXR_RELEASE_INPUTS_SCRIPT in job_block
    assert job_block.index(SOXR_RELEASE_INPUTS_SCRIPT) < job_block.index(
        "scripts/ci/build-release-artifacts.ps1"
    )


def test_release_workflow_prepares_soxr_release_inputs_before_build_and_publishes_source_bundle() -> (
    None
):
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    job_block = _workflow_job_block(workflow, "build-installer")

    assert SOXR_RELEASE_INPUTS_SCRIPT in job_block
    assert job_block.index(SOXR_RELEASE_INPUTS_SCRIPT) < job_block.index(
        "scripts/ci/build-release-artifacts.ps1"
    )
    assert SOXR_SOURCE_BUNDLE_NAME in workflow
    assert (
        "release-artifacts/installer_output/"
        "PuriPulyHeart-Setup-${{ needs.verify-version.outputs.version }}.exe" in workflow
    )
    assert (
        "release-artifacts/installer_output/"
        "PuriPulyHeart-Setup-${{ needs.verify-version.outputs.version }}.exe.sha256" in workflow
    )
    assert (
        "release-artifacts/build/soxr-release-inputs/"
        "PuriPulyHeart-soxr-third-party-source-bundle.zip" in workflow
    )


def test_readmes_require_prepare_soxr_release_inputs_before_pyinstaller() -> None:
    for readme_name in ("README.md", "README.ko.md", "README.ja.md", "README.zh-CN.md"):
        readme = (ROOT / readme_name).read_text(encoding="utf-8")

        assert README_SOXR_RELEASE_PREPARE_COMMAND in readme
        assert readme.index(README_SOXR_RELEASE_PREPARE_COMMAND) < readme.index(
            README_PYINSTALLER_BUILD_COMMAND
        )


def test_lgpl_text_file_exists_for_bundled_soxr_compliance_bundle() -> None:
    lgpl_text_path = ROOT / SOXR_LICENSE_TEXT_RELATIVE_PATH

    assert lgpl_text_path.is_file()

    lgpl_text = lgpl_text_path.read_text(encoding="utf-8")
    lgpl_lines = lgpl_text.splitlines()

    assert lgpl_lines[:2] == [
        "GNU LESSER GENERAL PUBLIC LICENSE",
        "Version 2.1, February 1999",
    ]
    assert "TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION" in lgpl_text
    assert "END OF TERMS AND CONDITIONS" in lgpl_text


def test_third_party_notices_cover_soxr_runtime_and_installed_compliance_bundle() -> None:
    notices = (ROOT / "src" / "puripuly_heart" / "data" / "THIRD_PARTY_NOTICES.txt").read_text(
        encoding="utf-8"
    )

    assert "Python-SoXR" in notices
    assert "libsoxr" in notices
    assert "soxr.dll" in notices
    assert SOXR_SOURCE_BUNDLE_NAME in notices
    assert "COPYING.LGPL-2.1.txt" in notices
    assert "third_party\\soxr\\" in notices
    assert "Installed releases include an LGPL compliance bundle under" in notices
    assert "exact python-soxr 1.0.0 and libsoxr 0.1.3 source archives used to build" in notices
    assert "{app}" not in notices


def test_shared_windows_build_script_runs_local_qwen_runtime_check_smoke() -> None:
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert (
        'Start-Process -FilePath $exePath -ArgumentList @("local-qwen-runtime-check") -Wait -PassThru'
        in script
    )
    assert "Local Qwen runtime smoke test failed" in script


def test_shared_windows_build_script_runs_soxr_runtime_check_smoke() -> None:
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert (
        'Start-Process -FilePath $exePath -ArgumentList @("soxr-runtime-check") -Wait -PassThru'
        in script
    )
    assert "soxr runtime smoke test failed" in script
    assert script.index("soxr-runtime-check") < script.index('"osc-send", "ci-smoke"')


def test_shared_windows_build_script_guards_packaged_soxr_dll_layout_and_source_bundle_contents() -> (
    None
):
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert '$packagedSoxrRuntimeDir = Join-Path $distDir "soxr"' in script
    assert '$packagedSoxrDllPath = Join-Path $packagedSoxrRuntimeDir "soxr.dll"' in script
    assert (
        '$packagedSoxrDlls = @(Get-ChildItem -Path $distDir -Filter "soxr.dll" -Recurse -File '
        "-ErrorAction SilentlyContinue)" in script
    )
    assert "if ($packagedSoxrDlls.Count -ne 1) {" in script
    assert "$packagedSoxrDlls[0].FullName" in script
    assert (
        '$stalePackagedLibsoxrDlls = @(Get-ChildItem -Path $distDir -Filter "libsoxr.dll" -Recurse '
        "-File -ErrorAction SilentlyContinue)" in script
    )
    assert "if ($stalePackagedLibsoxrDlls.Count -ne 0) {" in script
    assert (
        '$soxrReleaseInputsManifestPath = Join-Path $PWD "build/soxr-release-inputs/manifest.json"'
        in script
    )
    assert "ConvertFrom-Json" in script
    assert "[System.IO.Compression.ZipFile]::OpenRead($soxrSourceBundlePath)" in script
    assert '$sourceBundleArchive.GetEntry("manifest.json")' in script
    assert "$sourceBundleManifest.sources" in script


def test_shared_windows_build_script_stages_and_reinstalls_soxr_compliance_bundle() -> None:
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert (
        '$soxrLicenseTextPath = Join-Path $PWD "src\\puripuly_heart\\data\\licenses\\COPYING.LGPL-2.1.txt"'
        in script
    )
    assert '$packagedSoxrComplianceDir = Join-Path $distDir "third_party\\soxr"' in script
    assert (
        '$packagedSoxrLicensePath = Join-Path $packagedSoxrComplianceDir "COPYING.LGPL-2.1.txt"'
        in script
    )
    assert (
        "$packagedSoxrSourceBundlePath = Join-Path $packagedSoxrComplianceDir "
        "([System.IO.Path]::GetFileName($soxrSourceBundlePath))" in script
    )
    assert (
        '$installedSoxrComplianceDir = Join-Path $InstallerSmokeDir "third_party\\soxr"' in script
    )
    assert (
        '$installedSoxrLicensePath = Join-Path $installedSoxrComplianceDir "COPYING.LGPL-2.1.txt"'
        in script
    )
    assert (
        "$installedSoxrSourceBundlePath = Join-Path $installedSoxrComplianceDir "
        "([System.IO.Path]::GetFileName($soxrSourceBundlePath))" in script
    )
    assert "$expectedInstalledSoxrLicenseHash" in script
    assert "$expectedInstalledSoxrSourceBundleHash" in script
    assert "$reinstalledSoxrLicenseHash" in script
    assert "$reinstalledSoxrSourceBundleHash" in script
    assert (
        "Installed soxr LGPL license text reinstall smoke failed to restore bundled hash" in script
    )
    assert "Installed soxr source bundle reinstall smoke failed to restore bundled hash" in script


def test_shared_windows_build_script_runs_installed_app_soxr_runtime_check_after_installer_smoke() -> (
    None
):
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert '$installedExePath = Join-Path $InstallerSmokeDir "PuriPulyHeart.exe"' in script
    assert '$installedSoxrDllPath = Join-Path $InstallerSmokeDir "soxr\\soxr.dll"' in script
    assert (
        '$installedLegacySoxrDllPath = Join-Path $InstallerSmokeDir "soxr\\libsoxr.dll"' in script
    )
    assert (
        'Start-Process -FilePath $installedExePath -ArgumentList @("soxr-runtime-check") -Wait -PassThru'
        in script
    )
    assert "Installed app soxr runtime smoke test failed" in script
    assert (
        "Installed app still contains stale legacy soxr runtime DLL after installer smoke" in script
    )
    assert script.index("if ($installerSmoke.ExitCode -ne 0) {") < script.index(
        'Start-Process -FilePath $installedExePath -ArgumentList @("soxr-runtime-check") -Wait -PassThru'
    )


def test_shared_windows_build_script_reinstall_smoke_restores_official_soxr_runtime_and_compliance_bundle() -> (
    None
):
    script = (ROOT / "scripts" / "ci" / "build-release-artifacts.ps1").read_text(encoding="utf-8")

    assert (
        '$InstallerReinstallSmokeLogPath = Join-Path $env:TEMP "PuriPulyHeart-LocalSTT-Test-reinstall.log"'
        in script
    )
    assert "$expectedInstalledSoxrDllHash" in script
    assert "[System.IO.File]::WriteAllBytes($installedSoxrDllPath" in script
    assert "[System.IO.File]::WriteAllBytes($installedLegacySoxrDllPath" in script
    assert (
        "$mutatedInstalledSoxrDllHash = (Get-FileHash -Path $installedSoxrDllPath -Algorithm SHA256).Hash"
        in script
    )
    assert '"/LOG=$InstallerReinstallSmokeLogPath"' in script
    assert "$reinstalledSoxrDllHash" in script
    assert "Installed soxr runtime DLL reinstall smoke failed to restore bundled hash" in script
    assert "Installed stale legacy soxr runtime DLL was not removed by reinstall smoke" in script
    assert (
        script.count(
            'Start-Process -FilePath $installedExePath -ArgumentList @("soxr-runtime-check") -Wait -PassThru'
        )
        >= 2
    )


def test_shared_setup_action_installs_pinned_uv_and_uses_frozen_sync() -> None:
    action = (ROOT / ".github" / "actions" / "setup-uv-environment" / "action.yml").read_text(
        encoding="utf-8"
    )

    assert "uses: actions/setup-python@v5" in action
    assert "cache-dependency-path: uv.lock" in action
    assert '"uv==${{ inputs.uv-version }}"' in action
    assert "uv sync ${{ inputs.sync-args }} --frozen" in action


def test_installer_script_guards_against_repo_checkout_installs() -> None:
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert "#ifndef MyAppId" in script
    assert "AppId={#MyAppId}" in script
    assert r"DefaultDirName={autopf}\{#MyAppDirName}" in script
    assert "function DirectoryLooksLikeRepositoryCheckout(Path: String): Boolean;" in script
    assert "DirExists(AddBackslash(ProbePath) + '.git')" in script
    assert "FileExists(AddBackslash(ProbePath) + 'pyproject.toml')" in script
    assert "FileExists(AddBackslash(ProbePath) + 'AGENTS.md')" in script
    assert "procedure ResetSuspiciousInstallDir();" in script
    assert "if DirectoryLooksLikeRepositoryCheckout(CandidateDir) then begin" in script
    assert "Resetting suspicious install dir inside a repository checkout:" in script
    assert "WizardForm.DirEdit.Text := DefaultDir;" in script
    assert r"DefaultDir := ExpandConstant('{autopf}\{#MyAppDirName}');" in script
    assert "procedure InitializeWizard();" in script
    assert "function PrepareToInstall(var NeedsRestart: Boolean): String;" in script


def test_installer_script_copies_full_packaged_app_tree_without_legacy_internal_subdir_assumption() -> (
    None
):
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert (
        'Source: "{#MyPackagedAppDir}\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs'
        in script
    )
    assert 'Excludes: "{#MyAppExeName},{#MyOverlayExeName}"' in script
    assert 'Source: "{#MyPackagedAppDir}\\_internal\\*"' not in script


def test_installer_script_uses_root_level_local_stt_manifest_path_for_packaged_layout() -> None:
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert (
        '#define LocalSttManifestRelativePath "puripuly_heart\\data\\models\\qwen3-asr-0.6b-int8-sherpa.manifest.json"'
        in script
    )
    assert (
        '#define LocalSttManifestRelativePath "_internal\\puripuly_heart\\data\\models\\qwen3-asr-0.6b-int8-sherpa.manifest.json"'
        not in script
    )


def test_installer_script_guards_against_temporary_install_dirs() -> None:
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert "function DirectoryLooksLikeTemporaryLocation(Path: String): Boolean;" in script
    assert "TempRoot := RemoveBackslashUnlessRoot(GetEnv('TEMP'));" in script
    assert "TempRoot := RemoveBackslashUnlessRoot(GetEnv('TMP'));" in script
    assert (
        r"TempRoot := RemoveBackslashUnlessRoot(ExpandConstant('{localappdata}\Temp'));" in script
    )
    assert r"TempRoot := RemoveBackslashUnlessRoot(ExpandConstant('{tmp}'));" in script
    assert r"TempRoot := RemoveBackslashUnlessRoot(ExpandConstant('{win}\Temp'));" in script
    assert "if DirectoryLooksLikeTemporaryLocation(CandidateDir) then begin" in script


def test_installer_script_path_prefix_helper_handles_drive_root_boundaries() -> None:
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert r"(NormalizedRoot[Length(NormalizedRoot)] = '\')" in script


def test_installer_script_adds_local_stt_source_controls_and_provisioning_hook() -> None:
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert 'Source: "scripts\\installer\\install-local-stt-model.ps1"' in script
    assert "LocalSttSourcePage" in script
    assert "LocalSttSourceComboBox" in script
    assert "LocalSttReinstallCheckBox" in script
    assert "GetDefaultLocalSttSource" in script
    assert "ActiveLanguage() = 'chinesesimplified'" in script
    assert "RunLocalSttModelInstall" in script
    assert "install-local-stt-model.ps1" in script


def test_installer_script_supports_local_stt_appdata_override_for_smoke_runs() -> None:
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert "PURIPULY_HEART_LOCAL_STT_APPDATA_ROOT" in script
    assert "GetEnv('PURIPULY_HEART_LOCAL_STT_APPDATA_ROOT')" in script


def test_installer_script_deletes_managed_default_vad_cache_on_install() -> None:
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert "[InstallDelete]" in script
    assert 'Type: files; Name: "{localappdata}\\puripuly-heart\\silero_vad.onnx"' in script


def test_installer_script_deletes_legacy_installed_libsoxr_dll_on_install() -> None:
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")

    assert (
        'Source: "{#MyPackagedAppDir}\\*"; DestDir: "{app}"; '
        "Flags: ignoreversion recursesubdirs createallsubdirs" in script
    )
    assert 'Type: files; Name: "{app}\\soxr\\libsoxr.dll"' in script
    assert 'Excludes: "soxr\\soxr.dll"' not in script
    assert "Flags: ignoreversion onlyifdoesntexist" not in script


def test_local_stt_installer_script_uses_manifest_validation_and_atomic_promotion() -> None:
    script = (ROOT / "scripts" / "installer" / "install-local-stt-model.ps1").read_text(
        encoding="utf-8"
    )

    assert "Get-FileHash" in script
    assert "Invoke-WebRequest" in script
    assert "installed-manifest.json" in script
    assert "huggingface" in script.lower()
    assert "modelscope" in script.lower()
    assert "Move-Item" in script
    assert "selectedSource" in script or "SelectedSource" in script


def test_local_stt_installer_script_writes_bomless_manifest_and_treats_invalid_install_as_recoverable() -> (
    None
):
    script = (ROOT / "scripts" / "installer" / "install-local-stt-model.ps1").read_text(
        encoding="utf-8"
    )

    assert "UTF8Encoding($false)" in script
    assert "WriteAllText" in script
    assert "catch {" in script
    assert "return $false" in script


def test_local_stt_installer_script_attempts_backup_restore_on_promotion_failure() -> None:
    script = (ROOT / "scripts" / "installer" / "install-local-stt-model.ps1").read_text(
        encoding="utf-8"
    )

    assert '$backupDir = "$InstallDir.backup"' in script
    assert "Move-Item -Path $backupDir -Destination $InstallDir -Force" in script


def test_chinese_installer_language_files_use_matching_message_keys() -> None:
    pattern = re.compile(r"^([A-Za-z][A-Za-z0-9]*)=")

    def extract_keys(path: Path) -> set[str]:
        keys: set[str] = set()
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = pattern.match(line)
            if match:
                keys.add(match.group(1))
        return keys

    simplified = extract_keys(ROOT / "installer" / "Languages" / "ChineseSimplified.isl")
    traditional = extract_keys(ROOT / "installer" / "Languages" / "ChineseTraditional.isl")

    assert traditional == simplified
