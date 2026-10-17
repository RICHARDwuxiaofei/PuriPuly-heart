# Desktop model and microphone verification

Integration baseline: upstream `dev` at `196ddca3`. Environment: Windows, Python 3.14.7, locked development dependencies.

## Regression checks

```powershell
.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short tests/app tests/architecture tests/config tests/core tests/ui
```

Result: **6,933 passed, 9 skipped**, with 256 SDK/framework deprecation or compatibility warnings, in 607.94 seconds.

```powershell
.venv/Scripts/python.exe -m pytest -o addopts='' -q tests/core/test_explicit_model_sources.py tests/core/local_translation/test_model_sources.py tests/architecture/test_lifecycle_task_guard.py tests/ui/test_model_assets_selection.py tests/app/test_ui_provider_runtime_adapter.py tests/app/test_audio_recording.py tests/app/test_active_byok_secret_application.py
```

Result: **104 passed**, with one Google SDK deprecation warning. This selection covers explicit-source installation, cancellation/lease cleanup, local import integrity, combined E4B/26B import, model selection, recording/playback cleanup, and upstream active-BYOK behavior. The merged ASR validation calls were corrected to use the validator's `model_dir` parameter after the first run exposed two failures; the complete selection passed afterward.

`ruff check src tests` passed. Black checks passed for the final integration files.

An earlier repository-wide invocation reached 82% before its 900-second command deadline and did not produce a final suite result. It included failures from the pre-repair task registry and ASR validation calls. Those paths passed in the final affected-subsystem run above; a completed repository-wide result is not claimed.

## Actual runtime probes

All application probes used synthetic settings and a separate encrypted secret store in an OS temporary directory. Telemetry, OSC, integrated context, translation, and both capture channels were disabled. The application launcher rejected non-loopback connections. No private configuration, paid service, physical microphone, or production credential was used.

- The real headless host answered `capabilities`, `app status`, `models status`, and `settings choices`. Its catalog included both managed Gemma profiles. Both capture channels and translation remained off, and ordered shutdown exited the host.
- The real native Flet GUI rendered the normal dashboard without a secondary chat control or presentation child. The retained model dialog rendered with readable labels. A controlled UI-callback probe selected 26B local import without files; it displayed the missing-file failure without activating capture or translation. After correcting profile-specific copy, the final rendered error no longer required two files. Window-only screenshots were inspected locally.
- Restarted GUI composition retained the isolated local-source setting. Each observed GUI instance owned one visible window. CLI shutdown exited each host. Dialog actions were invoked through existing callbacks; mouse/keyboard accessibility is not certified by this probe.
- Throwaway small-asset probes exercised the real Gemma installer and SHA-256 validation for E4B's two assets and 26B's single asset. Corrupt imports were rejected without damaging installed models, and verified installations were reusable without reselecting files.
- A controlled synthetic ASR file source exercised both explicit endpoints, verified promotion, and reuse without another download. This proves installer behavior, not remote availability.
- The real recording buffer, microphone-session playback owner, and output adapter processed synthetic 48 kHz stereo PCM and 16 kHz mono PCM. Raw samples were preserved, processed samples matched the expected channel mix, and close released buffers/tasks. Output used a synthetic stream sink; no physical output device was opened.

Temporary launchers, fixture assets, and probe scripts are not part of the repository.

## Limits

Not verified: physical microphone/output behavior, full multi-GB downloads or model inference, mainland direct-download routing, game/VR operation, or redesigned settings/microphone UI. The retained surfaces still need their separately planned UI integration and polishing.

## Development refresh

Merged upstream `dev` through `22e19771`, including shared typefaces, the unified recent-language queue, and removal of obsolete experiment artifacts. The branch's retained model/audio scope and existing feature exclusions are unchanged. No additional runtime integration edits were needed.

```powershell
.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short tests/ui/test_typeface.py tests/ui/test_app_branches.py tests/ui/test_dashboard_view_branches.py tests/ui/test_display_card.py tests/ui/test_language_modal.py tests/ui/test_settings_view_branches.py tests/config/test_dual_target_settings.py tests/app/test_settings_application_owner.py tests/ui/test_model_assets_selection.py tests/app/test_audio_recording.py tests/app/test_active_byok_secret_application.py
```

Result: **595 passed**, with two deprecation warnings. `ruff check src tests` also passed. This is the refresh-specific selection, not another repository-wide run.

The real native GUI started from the refreshed source with the same isolated, offline settings/store setup described above. English and Korean dashboard rendering were inspected with window-only screenshots. CLI `settings.apply` returned terminal `applied` for the locale change; a new GUI instance retained Korean after restart. Translation and both capture channels remained off, and ordered shutdown exited both probe hosts. The temporary launcher was removed.
