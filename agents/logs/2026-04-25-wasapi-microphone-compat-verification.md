# WASAPI Microphone Default and Compatibility Mode Verification

## Verification Level

L2 local/mocked verification for settings migration, UI selection/display, GUI/headless runtime wiring, fallback flag isolation, and stream creation.

## Commands Run

1. `uv sync --extra dev`
2. `.\.venv\Scripts\python.exe -m pytest -q`
3. `.\.venv\Scripts\python.exe -m pytest tests/config/test_audio_host_api.py tests/config/test_config_and_secrets.py tests/core/test_audio_source.py tests/ui/test_audio_settings_host_api.py tests/ui/test_settings_view_branches.py tests/ui/test_controller_branch_paths.py tests/app/test_headless_mic_runner.py -q`
4. `.\.venv\Scripts\python.exe -m ruff check src/puripuly_heart/config/audio_host_api.py src/puripuly_heart/config/settings.py src/puripuly_heart/core/audio/source.py src/puripuly_heart/ui/components/settings/audio_settings.py src/puripuly_heart/ui/views/settings.py src/puripuly_heart/ui/controller.py src/puripuly_heart/app/headless_mic.py tests/config/test_audio_host_api.py tests/config/test_config_and_secrets.py tests/core/test_audio_source.py tests/ui/test_audio_settings_host_api.py tests/ui/test_settings_view_branches.py tests/ui/test_controller_branch_paths.py tests/app/test_headless_mic_runner.py`
5. `.\.venv\Scripts\python.exe -m black --check src/puripuly_heart/config/audio_host_api.py src/puripuly_heart/config/settings.py src/puripuly_heart/core/audio/source.py src/puripuly_heart/ui/components/settings/audio_settings.py src/puripuly_heart/ui/views/settings.py src/puripuly_heart/ui/controller.py src/puripuly_heart/app/headless_mic.py tests/config/test_audio_host_api.py tests/config/test_config_and_secrets.py tests/core/test_audio_source.py tests/ui/test_audio_settings_host_api.py tests/ui/test_settings_view_branches.py tests/ui/test_controller_branch_paths.py tests/app/test_headless_mic_runner.py`
6. `.\.venv\Scripts\python.exe -m black src/puripuly_heart/ui/components/settings/audio_settings.py tests/app/test_headless_mic_runner.py tests/ui/test_controller_branch_paths.py`
7. `.\.venv\Scripts\python.exe -m pytest tests/config/test_audio_host_api.py tests/config/test_config_and_secrets.py tests/core/test_audio_source.py tests/ui/test_audio_settings_host_api.py tests/ui/test_settings_view_branches.py tests/ui/test_controller_branch_paths.py tests/app/test_headless_mic_runner.py -q`
8. `.\.venv\Scripts\python.exe -m ruff check src/puripuly_heart/config/audio_host_api.py src/puripuly_heart/config/settings.py src/puripuly_heart/core/audio/source.py src/puripuly_heart/ui/components/settings/audio_settings.py src/puripuly_heart/ui/views/settings.py src/puripuly_heart/ui/controller.py src/puripuly_heart/app/headless_mic.py tests/config/test_audio_host_api.py tests/config/test_config_and_secrets.py tests/core/test_audio_source.py tests/ui/test_audio_settings_host_api.py tests/ui/test_settings_view_branches.py tests/ui/test_controller_branch_paths.py tests/app/test_headless_mic_runner.py`
9. `.\.venv\Scripts\python.exe -m black --check src/puripuly_heart/config/audio_host_api.py src/puripuly_heart/config/settings.py src/puripuly_heart/core/audio/source.py src/puripuly_heart/ui/components/settings/audio_settings.py src/puripuly_heart/ui/views/settings.py src/puripuly_heart/ui/controller.py src/puripuly_heart/app/headless_mic.py tests/config/test_audio_host_api.py tests/config/test_config_and_secrets.py tests/core/test_audio_source.py tests/ui/test_audio_settings_host_api.py tests/ui/test_settings_view_branches.py tests/ui/test_controller_branch_paths.py tests/app/test_headless_mic_runner.py`

## Outcome

- Command 1: PASS. Created the worktree project `.venv` and installed development dependencies.
- Command 2: FAIL before this feature work. Baseline full pytest had two pre-existing unrelated failures:
  - `tests/core/test_managed_identity.py::test_ensure_managed_identity_bundle_generates_uuid7_and_keeps_secret_boundary`
  - `tests/ui/test_settings_view_branches.py::test_custom_vocabulary_switching_source_language_updates_editor_payload`
- Command 3: PASS. Focused L2 pytest suite completed successfully.
- Command 4: PASS. Ruff reported `All checks passed!`.
- Command 5: FAIL. Black reported three files would be reformatted.
- Command 6: PASS. Black reformatted the three reported files.
- Command 7: PASS. Focused L2 pytest suite completed successfully after formatting.
- Command 8: PASS. Ruff reported `All checks passed!` after formatting.
- Command 9: PASS. Black reported all 14 touched files would be left unchanged.

## Skipped

- No live microphone device smoke test was run; verification is local/mocked.
- Full baseline pytest was not used as completion evidence because it failed before this feature work with the unrelated failures listed above. The focused L2 suite covering touched areas passed after implementation and formatting.

## Notes

- Confirms schema migration, default Host API, UI compatibility mode selection/display, i18n labels, runtime normalization, fallback flag isolation, and WASAPI extra settings behavior.
- Compatibility-mode fallback was additionally verified for same-device name fallback retry without WASAPI flags.
