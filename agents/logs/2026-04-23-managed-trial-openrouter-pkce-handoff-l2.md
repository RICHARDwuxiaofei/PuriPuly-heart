# Managed trial OpenRouter PKCE handoff L2 verification

- Verification level: L2
- Worktree: `C:\Users\salee\Documents\dev\puripuly_heart\.worktrees\managed-trial-pkce-handoff`

## Commands run

1. `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/app/test_wiring_providers.py::test_create_llm_provider_openrouter_byok_still_uses_user_owned_secret_after_pkce_storage -ra`
   - PASS — `1 passed in 0.15s`
2. `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/config/test_managed_identity_settings.py tests/core/test_openrouter_handoff.py tests/core/test_openrouter_pkce.py tests/core/test_managed_openrouter_release.py tests/ui/test_settings_view_branches.py tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_founder_letter_dialog.py tests/app/test_wiring_providers.py tests/providers/test_openrouter_provider.py -ra`
   - PASS — `534 passed in 5.10s`
3. `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/config/test_managed_identity_settings.py tests/core/test_openrouter_handoff.py tests/core/test_openrouter_pkce.py tests/core/test_managed_openrouter_release.py tests/ui/test_settings_view_branches.py tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_founder_letter_dialog.py tests/app/test_wiring_providers.py tests/providers/test_openrouter_provider.py -q`
   - PASS — `367 passed, 0 failed`
4. `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe -m pytest tests/config/test_managed_identity_settings.py tests/core/test_openrouter_handoff.py tests/core/test_openrouter_pkce.py tests/core/test_managed_openrouter_release.py tests/ui/test_settings_view_branches.py tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/ui/test_founder_letter_dialog.py tests/app/test_wiring_providers.py tests/providers/test_openrouter_provider.py -q`
   - PASS — `535 passed, 0 failed`

## Outcome

PASS

## Skipped items and reason

- Browser-loopback PKCE manual smoke is not automated in pytest; kept as a manual follow-up only.

## Notes

- `selection_alias` stays canonical.
- Manual OpenRouter connect remains hidden behind the break-glass gate and is not documented as the normal continuation path.
- The BYOK wiring regression now proves the user-owned `openrouter_api_key` still wins even when both BYOK and managed OpenRouter secrets are present.
- Final rerun includes the modal founder-letter fix; the dialog is no longer dismissible outside the two CTA actions.
