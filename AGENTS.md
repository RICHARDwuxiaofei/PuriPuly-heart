## Purpose & Authority

- This file is the authority for **agent operating policy** in this repository.
- Discoverable implementation facts (exact defaults, command options, file formats) must be read from code and README files first.
- If implementation facts and docs disagree, treat code as the source of truth and then align docs.
- Keep this file focused on rules and routing, not long feature or setup explanations.

## Agent-Only Invariants

- Settings compatibility is mandatory:
  - Keep `to_dict` and `from_dict` synchronized.
  - New settings must have defaults so existing `settings.json` continues loading.
  - If a setting key is renamed, accept the old key in `from_dict` for backward compatibility.
- All new user-facing UI text must go through i18n keys, and all locale bundles must be updated.
- For documentation lookup and code generation, prefer MCP resources/templates first and use Context7 when available.
- For browser or website automation tasks, use the `agent-browser` skill first.

## Task Router (Canonical Paths)

- Settings schema, defaults, validation:
  - `src/puripuly_heart/config/settings.py`
  - `src/puripuly_heart/config/paths.py`
  - `src/puripuly_heart/ui/views/settings.py`
- Prompt behavior and provider prompt loading:
  - `src/puripuly_heart/config/prompts.py`
  - `prompts/`
- Provider interfaces and runtime wiring:
  - `src/puripuly_heart/core/stt/backend.py`
  - `src/puripuly_heart/core/llm/provider.py`
  - `src/puripuly_heart/providers/stt/`
  - `src/puripuly_heart/providers/llm/`
  - `src/puripuly_heart/app/wiring.py`
- Orchestrator behavior and context memory:
  - `src/puripuly_heart/core/orchestrator/hub.py`
  - `tests/core/test_context_memory.py`
- Build and release touchpoints:
  - `build.spec`
  - `installer.iss`
  - `pyproject.toml`
  - `src/puripuly_heart/__init__.py`

## Verification Contract & Evidence

- Verification is mandatory before claiming completion.
- Choose a level by risk and escalate when in doubt.
  - L0: Fast, relevant checks for touched area.
  - L1: L0 plus broader local confidence checks.
  - L2: Unit tests plus integration tests when feasible.
  - L3: L2 plus Windows build/installer verification using project `.venv`.
- Default escalation guidance:
  - Code changes: at least L1.
  - Provider, prompt, OSC, wiring changes: at least L2.
  - Release/installer/build config changes: L3.
- Skips are allowed only for real environment constraints; run the best lower level and record what was skipped and why.
- Store verification evidence in `agents/logs/` as short Markdown notes including:
  - Verification level.
  - Commands run.
  - PASS/FAIL outcome.
  - Skipped items and reason (if any).
  - Notes for key assumptions or mismatch resolution.
- Do not encode verification detail inside commit messages.

## Security & Async Safety

- Keep provider and I/O calls async; avoid blocking the event loop.
- Use `asyncio.create_task` for long-running loops and ensure cancellation on shutdown.
- Always `await` provider `close()` in teardown paths.
- In Flet UI callbacks, use `page.run_task` for async work.
- Secrets are loaded through `SecretStore` (keyring/encrypted file/env fallback).
- When `secrets.backend` is `encrypted_file`, require `PURIPULY_HEART_SECRETS_PASSPHRASE`.
- Never commit real credentials, API keys, or secret material.

## Freshness Guardrails

- Do not hardcode volatile defaults or file format assumptions in this file.
- Prompt file naming/extensions and fallback order must be verified in `src/puripuly_heart/config/prompts.py`.
- Orchestrator default parameters (including context memory values) must be verified in `src/puripuly_heart/core/orchestrator/hub.py`.
- Keep guidance concise and non-duplicative. Prefer routing to canonical paths over re-documenting details already in code/README.
