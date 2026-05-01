# Verification Note

- Verification level: L3
- Date: 2026-04-13
- Scope: Task 5 OpenVR DLL vendoring verification

## Commands Run

```powershell
& 'C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe' -m pytest tests/core/test_openvr_vendor.py tests/app/test_overlay_process_manager.py tests/ui/test_controller_branch_paths.py::test_overlay_start_failure_preserves_specific_preflight_reason tests/ui/test_settings_view_branches.py::test_overlay_failure_reason_keys_are_localized tests/app/test_release_dependency_guards.py -q
```

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'C:\Users\salee\Documents\dev\puripuly_heart\.venv'
$env:APP_VERSION = (& 'C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe' 'scripts/ci/read-project-version.py').Trim()
& '.\scripts\ci\prepare-soxr-release-inputs.ps1'
& '.\scripts\ci\build-release-artifacts.ps1' -AppVersion $env:APP_VERSION -InnoSetupVersion '6.6.1'
```

```powershell
& 'C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe' -m pytest tests/core/test_openvr_vendor.py tests/app/test_overlay_process_manager.py tests/ui/test_controller_branch_paths.py::test_overlay_start_failure_preserves_specific_preflight_reason tests/ui/test_settings_view_branches.py::test_overlay_failure_reason_keys_are_localized tests/app/test_release_dependency_guards.py --collect-only -q
```

```powershell
$localAppData = [Environment]::GetFolderPath('LocalApplicationData')
$tempDir = $env:TEMP
(Get-FileHash -Path '.\third_party\openvr\win64\openvr_api.dll' -Algorithm SHA256).Hash.ToLowerInvariant()
(Get-FileHash -Path '.\build\overlay\openvr_api.dll' -Algorithm SHA256).Hash.ToLowerInvariant()
(Get-FileHash -Path '.\dist\PuriPulyHeart\openvr_api.dll' -Algorithm SHA256).Hash.ToLowerInvariant()
(Get-FileHash -Path (Join-Path $localAppData 'Programs\PuriPulyHeart-LocalSTT-Test\openvr_api.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
Select-String -Path (Join-Path $tempDir 'PuriPulyHeart-LocalSTT-Test.log') -Pattern 'Local STT provisioning completed successfully\.' -Quiet
Select-String -Path (Join-Path $tempDir 'PuriPulyHeart-LocalSTT-Test-reinstall.log') -Pattern 'Local STT provisioning completed successfully\.' -Quiet
& '.\build\overlay\PuriPulyHeartOverlay.exe' --check-startup-contract
& '.\dist\PuriPulyHeart\PuriPulyHeartOverlay.exe' --check-startup-contract
```

## Outcome

- PASS: focused Python/UI/guard pytest slice passed from the worktree (`115` collected, test run exit code `0`).
- PASS: `APP_VERSION` resolved to `2.0.0` via `scripts/ci/read-project-version.py`.
- PASS: `scripts/ci/prepare-soxr-release-inputs.ps1` completed and produced `build/soxr-release-inputs/manifest.json`.
- PASS: `scripts/ci/build-release-artifacts.ps1 -AppVersion 2.0.0 -InnoSetupVersion 6.6.1` completed end-to-end, including staged packaging, installer build, alternate-AppId install smoke, and reinstall repair smoke.

## Skipped Items

- None.

## Key Evidence

- Vendored OpenVR DLL SHA256: `bab8ac6ef64e68a9ca53315b0014d131088584b2efdfa6db511d67ec03cfcb4a`
- Staged OpenVR DLL SHA256: `bab8ac6ef64e68a9ca53315b0014d131088584b2efdfa6db511d67ec03cfcb4a`
- Packaged OpenVR DLL SHA256: `bab8ac6ef64e68a9ca53315b0014d131088584b2efdfa6db511d67ec03cfcb4a`
- Installed OpenVR DLL SHA256 after install/reinstall smoke: `bab8ac6ef64e68a9ca53315b0014d131088584b2efdfa6db511d67ec03cfcb4a`
- Packaged DLL hash match: PASS (`packaged == vendored`)
- Installed DLL hash match: PASS (`installed == vendored` after reinstall smoke)
- Reinstall repair result: PASS (`PuriPulyHeart-LocalSTT-Test-reinstall.log` contains `Local STT provisioning completed successfully.` and the installed OpenVR DLL hash was restored to the pinned vendored hash after the script intentionally mutated it)
- Startup-contract smoke result: PASS
  - Staged overlay: exit `0`, output `{"app_version":"2.0.0","contract_version":5}`
  - Packaged overlay: exit `0`, output `{"app_version":"2.0.0","contract_version":5}`

## Notes

- The packaging script also completed its bundled Local Qwen runtime, soxr runtime, compliance bundle, installer, and repair-path checks before returning success.
- Non-fatal upstream/toolchain warnings were emitted during the soxr CMake build, but they did not change the PASS result because the scripted L3 verification completed with exit code `0` and produced the expected artifacts.
