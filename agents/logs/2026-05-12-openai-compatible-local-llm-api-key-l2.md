# OpenAI-Compatible Local LLM API Key Verification

## Verification level

L2 - focused provider, settings, app wiring, controller integration, UI, i18n, and lint checks.

## Commands run

- `.venv\Scripts\python.exe -m pytest tests/providers/test_local_openai_provider.py tests/config/test_config_and_secrets.py tests/app/test_wiring_providers.py tests/ui/test_api_key_field.py tests/ui/test_settings_view_branches.py tests/ui/test_app_branches.py tests/ui/test_controller_api_verification.py tests/ui/test_controller_branch_paths.py tests/ui/test_i18n_key_usage.py -q`
- `.venv\Scripts\python.exe -m pytest`
- `.venv\Scripts\python.exe -m ruff check src/puripuly_heart/core/storage/secrets.py src/puripuly_heart/providers/llm/local_openai.py src/puripuly_heart/app/wiring.py src/puripuly_heart/ui/components/settings/api_key_field.py src/puripuly_heart/ui/views/settings.py src/puripuly_heart/ui/controller.py src/puripuly_heart/ui/app.py tests/providers/test_local_openai_provider.py tests/config/test_config_and_secrets.py tests/app/test_wiring_providers.py tests/ui/test_api_key_field.py tests/ui/test_settings_view_branches.py tests/ui/test_app_branches.py tests/ui/test_controller_api_verification.py tests/ui/test_controller_branch_paths.py tests/ui/test_i18n_key_usage.py`
- `git diff 9e8c67c1c830dcfab118a7c589b2c8a51f6f24db -- src tests docs agents`
- `git diff --name-only 9e8c67c1c830dcfab118a7c589b2c8a51f6f24db -- src tests docs agents`
- `git diff --check 9e8c67c1c830dcfab118a7c589b2c8a51f6f24db -- src tests docs agents`
- `git status --short`
- `git diff --no-index -- /dev/null tests/ui/test_api_key_field.py`
- Secret-pattern scans of the saved base diff output, including an added-lines-only scan.

## Outcome

- PASS: targeted pytest suite exited 0 with no failures reported after the final review fixes. A test-run subagent attempt used a non-venv `py -m pytest` environment and produced unrelated Flet-version failures; the recorded PASS is from the required direct `.venv\Scripts\python.exe` command.
- PASS: full pytest suite exited 0: `2206 passed, 9 skipped in 34.66s`.
- PASS: ruff exited 0 with `All checks passed!`.
- PASS: working-tree diff from base SHA `9e8c67c1c830dcfab118a7c589b2c8a51f6f24db` is limited to the expected `src/` and `tests/` implementation/test files for Local LLM API-key support, including the reviewed `src/puripuly_heart/core/storage/secrets.py` delete-failure fix.
- PASS: `git status --short` shows `tests/ui/test_api_key_field.py` as an untracked new file; `git diff --no-index -- /dev/null tests/ui/test_api_key_field.py` was run so the new file is included in review evidence despite no staging/commit.
- PASS: `git diff --check` reported no whitespace errors.
- PASS: added-lines-only diff secret scan reported `no high-risk secret patterns found in added lines`. A broader context scan matched only an existing dummy test fixture value `passphrase="pw"`, not real secret material.

## Skipped items

- Full installer and Rust overlay rebuild were not run because this change does not modify Rust, native overlay, installer, or release packaging files.
- Broker Node verification was not run because broker files are not touched.

## Notes

- The Local LLM connection remains internally compatible with persisted `ollama` enum/i18n keys while presenting the user-facing connection as OpenAI-compatible API.
- `local_llm_api_key` remains SecretStore-only and is not serialized into settings.
- `LOCAL_LLM_API_KEY` is intentionally ignored for Local LLM provider construction and connection verification.
- Unexpected Keyring delete failures now propagate so Local LLM secret clears cannot falsely report success and rebuild with a stale key still present. A keyring `PasswordDeleteError` is suppressed only after `get_password()` confirms the secret is absent.
- Per repository policy, no commits were created because the user has not explicitly authorized committing.
