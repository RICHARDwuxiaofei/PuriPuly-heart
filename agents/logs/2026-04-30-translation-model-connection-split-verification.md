# Translation Model Connection Split Verification

Date: 2026-05-01

## Level

L2: settings serialization tests, Settings UI branch tests, controller/app compatibility tests, provider wiring tests, and OpenRouter provider routing tests.

## Commands

- `.\.venv\Scripts\python -m pytest tests/ui/test_settings_prompt_switching.py tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/app/test_wiring_providers.py tests/providers/test_openrouter_provider.py -q`
- `.\.venv\Scripts\python -m pytest tests/config/test_config_and_secrets.py tests/ui/test_settings_view_branches.py tests/ui/test_settings_prompt_switching.py tests/ui/test_controller_branch_paths.py tests/ui/test_app_branches.py tests/app/test_wiring_providers.py tests/providers/test_openrouter_provider.py -q`

## Outcome

PASS. Focused controller/app/provider compatibility batch passed with exit status 0. Full L2 verification batch passed with exit status 0 and failed 0.

## Skipped

None.

## Notes

- Qwen 3.5 Flash remains available as OpenRouter fallback only.
- Existing OpenRouter routing mode remains serialized and used by OpenRouter runtime requests.
