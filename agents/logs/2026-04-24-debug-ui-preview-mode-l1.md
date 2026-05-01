# Debug UI preview mode verification

- Date: 2026-04-24
- Verification level: L1
- Workdir: C:\Users\salee\Documents\dev\puripuly_heart\.worktrees\debug-ui-preview-mode

## Commands run

During the first formatting check, `black --check` reported drift:

- `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m black --check src/puripuly_heart/main.py src/puripuly_heart/ui/app.py src/puripuly_heart/ui/components/debug_preview_panel.py tests/app/test_main_cli.py tests/ui/test_debug_preview_panel.py tests/ui/test_app_branches.py`
  - Result: FAIL, 4 files would be reformatted and 2 files would be left unchanged.
- `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m black src/puripuly_heart/main.py src/puripuly_heart/ui/app.py src/puripuly_heart/ui/components/debug_preview_panel.py tests/app/test_main_cli.py tests/ui/test_debug_preview_panel.py tests/ui/test_app_branches.py`
  - Result: PASS, 4 files reformatted and 2 files left unchanged.

After applying formatting, the required verification sequence was rerun and passed:

- `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/app/test_main_cli.py tests/ui/test_debug_preview_panel.py tests/ui/test_app_branches.py -ra`
  - Result: PASS, 74 passed in 0.67s.
- `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/app/test_main_cli.py tests/ui/test_debug_preview_panel.py tests/ui/test_app_branches.py tests/ui/test_founder_letter_dialog.py tests/ui/test_controller_branch_paths.py tests/ui/test_settings_view_branches.py tests/config/test_managed_identity_settings.py tests/core/test_openrouter_handoff.py tests/core/test_openrouter_pkce.py -ra`
  - Result: PASS, 466 passed in 5.06s.
- `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m ruff check src/puripuly_heart/main.py src/puripuly_heart/ui/app.py src/puripuly_heart/ui/components/debug_preview_panel.py tests/app/test_main_cli.py tests/ui/test_debug_preview_panel.py tests/ui/test_app_branches.py`
  - Result: PASS, all checks passed.
- `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m black --check src/puripuly_heart/main.py src/puripuly_heart/ui/app.py src/puripuly_heart/ui/components/debug_preview_panel.py tests/app/test_main_cli.py tests/ui/test_debug_preview_panel.py tests/ui/test_app_branches.py`
  - Result: PASS, 6 files would be left unchanged.

## Skips

- Manual GUI visual QA itself was skipped because this API/session cannot interact with desktop Flet windows. A human/developer can run these supported manual inspection commands from the worktree:
  - `puripuly-heart --debug-ui-preview`
  - `puripuly-heart run-gui --debug-ui-preview`

## Notes

- Preview actions are app-owned and use existing Dashboard/founder-letter/snackbar rendering.
- Preview action tests guard against settings saves, production founder-letter CTA side effects, PKCE launch, and external URL opens.
- `DebugPreviewPanel` boundary test prevents imports of settings, secrets, broker/OpenRouter, and browser modules.
