PuriPuly <3 Agent Instructions
==============================

## 1. Overview

### 1.1 Project Overview

PuriPuly <3 is a VRChat real-time speech translation pipeline:
audio/VAD -> STT -> LLM -> OSC. It ships a GUI using Flet and CLI entry points.

### 1.2 Development Environment

- Python >=3.12,<3.14. See `pyproject.toml`.
- Install: `pip install -e .`
- Dev install: `pip install -e '.[dev]'`
- Entry point: `python -m puripuly_heart.main ...`
- Prefer running tests and CLI commands inside a local project virtual environment.
- The virtual environment is not committed to git, so use the local venv on the machine.

## 2. Quick Reference File Map

### 2.1 Repository Structure

- `src/puripuly_heart/app/` CLI and headless runners for stdin and mic
- `src/puripuly_heart/core/` orchestrator, audio, VAD, OSC, STT, LLM
- `src/puripuly_heart/providers/` concrete STT and LLM providers
- `src/puripuly_heart/ui/` Flet UI, views, controller
- `src/puripuly_heart/config/` settings, prompts, paths
- `src/puripuly_heart/data/` packaged assets (fonts, pictures, VAD model, third-party notices)
- `src/puripuly_heart/data/i18n/` UI localization bundles
- `prompts/` system prompt files
- `tests/` unit/component tests (organized by area)
  - `tests/app/` app wiring and headless runners
  - `tests/core/` core pipeline, audio, VAD, STT, OSC, orchestrator
  - `tests/config/` settings, prompts, secrets
  - `tests/providers/` provider unit tests and client contracts
  - `tests/domain/` domain models/events
  - `tests/ui/` UI tests (WIP, optional)
  - `tests/smoke/` import and lightweight smoke checks
  - `tests/helpers/` shared fakes and fixtures
- `tests/integration/` opt-in integration tests

### 2.2 Configuration and Settings

- Settings schema and validation: `src/puripuly_heart/config/settings.py`
- Default settings path: `src/puripuly_heart/config/paths.py`
- Settings are JSON. `to_dict` and `from_dict` must stay in sync.
- If you add or change settings:
  - Update dataclasses and validation in `src/puripuly_heart/config/settings.py`
  - Update `to_dict` and `from_dict`
  - Wire UI fields in `src/puripuly_heart/ui/views/settings.py`
- New settings must have defaults so existing settings.json loads without error
- If renaming a setting, accept the old key in from_dict for backward compatibility

### 2.3 Prompts

- Prompt loader: `src/puripuly_heart/config/prompts.py`
- Prompts are loaded from `prompts/` or `PURIPULY_HEART_PROMPTS_DIR`
- Provider prompt files: `prompts/gemini.txt`, `prompts/qwen.txt`
- If you add a new default prompt or LLM provider, add a matching file under `prompts/`

### 2.4 i18n

- All new user-facing text must go through the i18n layer (use translation keys, not hardcoded strings).
- Update every locale bundle under `src/puripuly_heart/data/i18n/` when adding keys.

### 2.5 Providers

- STT interface: `src/puripuly_heart/core/stt/backend.py`
- LLM interface: `src/puripuly_heart/core/llm/provider.py`
- Implement providers under:
  - `src/puripuly_heart/providers/stt/`
  - `src/puripuly_heart/providers/llm/`
- Provider selection wiring: `src/puripuly_heart/app/wiring.py`
- Provider settings and enums: `src/puripuly_heart/config/settings.py`

### 2.6 Orchestrator and Hub

- Core pipeline coordinator is `ClientHub` in `src/puripuly_heart/core/orchestrator/hub.py`
- Flow: audio and VAD events -> STT -> optional LLM translation -> OSC queue -> UI events
- Owns task lifecycles for the STT event loop and OSC flushing
- `start` and `stop` manage cancellation and provider shutdown

### 2.7 Context Memory

- Implemented in `ClientHub` as `_translation_history`
- Defaults: `context_time_window_s = 20.0`, `context_max_entries = 3`
- Only recent entries within the time window are formatted and passed to the LLM
- Update `tests/core/test_context_memory.py` when changing window size or behavior

## 3. Verification Policy

### 3.1 Verification Contract

- Verification is mandatory. The agent must not claim completion without running an appropriate verification level.
- The agent chooses concrete commands and scope, but must leave evidence under `agents/logs/`.
- Do not encode verification in commit messages.

### 3.2 Verification Ladder

- L0 per edit: at least one fast, relevant check for the touched area
- L1 pre-commit: L0 plus broader local confidence for cross-cutting changes
- L2 share ready: full unit tests plus integration tests when feasible for code changes
- L3 release: L2 plus Windows build verification using the project `.venv`

### 3.3 Escalation Triggers

- If in doubt, move up one level.
- Code changes should default to L1 or higher.
- Provider, prompt, OSC, and wiring changes should move to L2.
- Release, installer, and build configuration changes should move to L3.

### 3.4 Exceptions and Skips

- Skips are allowed when a level cannot run due to missing credentials, hardware, or environment setup.
- When skipping, run the best available lower level and record what was skipped and why.

### 3.5 Verification Evidence

- Store verification evidence as short Markdown notes under `agents/logs/`.
- Each note should include the verification level, commands run, and pass or fail outcome.
- Verification log files under `agents/logs/` are ignored by git.

### 3.6 Verification Toolbox

- Pre-commit, also run by the git hook: `pre-commit run --all-files`
- Unit tests: `python -m pytest` (use the venv interpreter or an activated venv)
- Integration tests: `INTEGRATION=1 python -m pytest` (use the venv interpreter or an activated venv)
- Integration tests require provider credentials. See `README.md`.
- CLI smoke: `python -m puripuly_heart.main --help` (use the venv interpreter or an activated venv)
- Build verification: run the build commands in Build and Distribution

## 4. Build and Distribution

### 4.1 Build Artifacts Overview

- PyInstaller builds are configured in `build.spec`
- Windows installer is configured in `installer.iss`
- Package data lives under `src/puripuly_heart/data/`

### 4.2 PyInstaller Build

- Always use the project `.venv` for builds to ensure correct dependencies
- Build app: `.venv\Scripts\pyinstaller.exe build.spec`

### 4.3 Installer Build

- Build the installer: `ISCC installer.iss`

### 4.4 Packaging Notes

- Provider modules may require PyInstaller hidden imports in `build.spec`

## 5. Change Checklists

### 5.1 Release Checklist

- Bump versions in `pyproject.toml`, `src/puripuly_heart/__init__.py`, and `installer.iss` MyAppVersion
- Build the app on Windows
- Build the installer
- Tag the release on `main`: `git tag vX.Y.Z` and publish artifacts

### 5.2 Provider Change Checklist

- Implement provider under `src/puripuly_heart/providers/` to keep the shared interface working across the app
- Update enums, defaults, and validation in `src/puripuly_heart/config/settings.py` so settings can select and persist it
- Update `to_dict` and `from_dict` for settings schema changes
- Wire UI choices in `src/puripuly_heart/ui/views/settings.py` so users can configure it
- Update provider wiring in `src/puripuly_heart/app/wiring.py` so runtime selection resolves to the new class
- Add prompts in `prompts/{provider}.txt` to keep output quality consistent for LLM providers
- Add PyInstaller hidden imports in `build.spec` so bundled builds include dynamic provider modules
- Document secrets and env vars in `README.md` so users can run it without guesswork
- Add dependencies in `pyproject.toml` to avoid runtime import errors

## 6. MCP Usage

### 6.1 External Documentation

- Prefer MCP resources and templates over web search or guessing.
- For library docs, setup steps, or code generation, use Context7 when available.
- For Flet, check both `/websites/flet_dev` and `/flet-dev/flet` sources.

## 7. Engineering Rules

### 7.1 Async Patterns

- Keep I/O and provider calls async and avoid blocking the event loop
- Run long-lived loops with `asyncio.create_task` and ensure they are cancelled on shutdown
- Always `await` provider `close` in teardown paths
- In UI, use `page.run_task` for async work instead of blocking callbacks

### 7.2 Secrets and Security

- SecretStore reads from keyring or encrypted file, then falls back to env vars
- Known keys and env vars are documented in `README.md`
- If `secrets.backend` is `encrypted_file`, require `PURIPULY_HEART_SECRETS_PASSPHRASE`
- Never commit real credentials or API keys

### 7.3 Team Rules

- Branch strategy: trunk-based on `main` with no long-lived release branches
- Merge strategy: squash, one commit per PR or topic
- Keep `main` releasable and release by tagging `main` with `vX.Y.Z`
- Style tools: format with `black`, lint with `ruff` via `pre-commit`
