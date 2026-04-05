## Verification

- Level: L3 (attempted)
- Outcome: PARTIAL

## Commands

- `.venv-wsl/bin/python -m pytest tests/app/test_release_dependency_guards.py -k temporary_install_dirs`
- `.venv-wsl/bin/python -m pytest tests/app/test_release_dependency_guards.py -k 'temporary_install_dirs or drive_root_boundaries or repo_checkout_installs'`
- `.venv-wsl/bin/python -m pytest tests/app/test_release_dependency_guards.py`
- `.venv/Scripts/python.exe -m pytest tests/app/test_release_dependency_guards.py -k "temporary_install_dirs or drive_root_boundaries or repo_checkout_installs"`
- `.venv/Scripts/python.exe -m pytest tests/app/test_release_dependency_guards.py`
- `C:\Program Files (x86)\Inno Setup 6\ISCC.exe installer.iss`
- `powershell.exe -NoProfile -Command -` registry query for uninstall entries and temp-path matches

## Results

- PASS: targeted regression `test_installer_script_guards_against_temporary_install_dirs`
- PASS: targeted installer guard subset on WSL and Windows Python
- FAIL: full `tests/app/test_release_dependency_guards.py` in WSL and Windows due missing `scripts/installer/install-local-stt-model.ps1`
- FAIL: `ISCC.exe installer.iss` for the same missing file
- PASS: current production uninstall registry entry resolves to `C:\Users\salee\AppData\Local\Programs\PuriPulyHeart` and did not match temp roots at inspection time

## Notes

- Root cause investigated before fix: `UsePreviousAppDir=yes` reuses prior install path by `AppId`, while `ResetSuspiciousInstallDir()` only rejected repository checkouts and did not reject temp-root installs.
- Existing smoke-test isolation with alternate `AppId` was added later in `scripts/ci/build-release-artifacts.ps1`; temp-path reuse remained possible for any pre-existing production `AppId` entry pointing into `%TEMP%`.
- The new installer change adds a temp-location guard using `TEMP`, `TMP`, `{localappdata}\Temp`, `{tmp}`, and `{win}\Temp`.
- Follow-up review found a drive-root edge case in `PathEqualsOrIsUnder`; the helper now treats roots ending with `\` as valid parent boundaries and tests now assert the repo branch plus all intended temp-root checks remain present.
- Refreshed immediately before commit:
  - `UV_PROJECT_ENVIRONMENT=.venv-wsl .venv-wsl/bin/python -m pytest tests/app/test_release_dependency_guards.py -k 'temporary_install_dirs or drive_root_boundaries or repo_checkout_installs'` passed.
  - `.venv/Scripts/python.exe -m pytest tests/app/test_release_dependency_guards.py -k "temporary_install_dirs or drive_root_boundaries or repo_checkout_installs"` passed.
  - `ISCC installer.iss` still stops at the pre-existing missing file `scripts/installer/install-local-stt-model.ps1`.

## Skipped

- Full Windows installer smoke install was not run because the installer does not currently compile: missing `scripts/installer/install-local-stt-model.ps1`.
