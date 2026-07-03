# Bundle C Spec: vnext Settings-heavy Fallback & Telemetry

작성일: 2026-07-03
대상 기준선: `vnext`
비교 기준: `dev` vs `vnext`, merge-base `2391e4877860a75c9bbc847074ae813a9467414e`
예상 작업 브랜치: `work/vnext-port-settings-heavy`
권장 worktree: `puripuly_heart-wt-settings-heavy`

## 1. Goal

settings/persistence/runtime resolution/UI settings를 크게 건드리는 `dev` 기능들을 `vnext` 아키텍처에 맞춰 재구현한다.

이 번들은 다음 2개 큰 변경을 묶는다.

1. Curated fallback presets/defaults/UX 최신화
2. App-side telemetry consent/runtime shell

이 번들은 가장 큰 작업 단위이며, `vnext`의 settings intent/state/runtime separation 원칙을 지켜야 한다.

## 2. Why this is one bundle

Fallback과 telemetry는 모두 다음 파일/경계를 건드릴 가능성이 크다.

- settings schema
- settings migration/serialization
- runtime resolution
- settings view
- app/controller settings sync
- i18n bundles
- settings-related tests

이 둘을 병렬 worktree로 분리하면 `settings.py`, `settings_vnext/*`, `ui/views/settings.py`, i18n JSON, settings tests에서 conflict가 매우 커진다. 따라서 하나의 settings-heavy bundle에서 순차적으로 처리한다.

## 3. Source refs and evidence

### Fallback source refs

- `1b3e484 feat(llm): add curated translation fallbacks`
- `c72ea2c fix(config): use new fallback defaults on first run`
- `33e89cb feat(ui): split LLM translation modal into recommended and others sections`
- `2052d48 refactor(llm): reorder fallback options, remove option descriptions, split modal title`
- `0660c31 fix(ui): hide DeepSeek China fallback option, add Cerebras Gemma description`
- dev `src/puripuly_heart/config/settings.py`
  - `TranslationFallbackSelectionAlias`
  - `translation.fallback_selection_alias`
  - first-run fallback defaults
- dev `src/puripuly_heart/config/llm_profiles.py`
  - curated fallback profile mappings
- dev `src/puripuly_heart/ui/views/settings.py`
  - fallback modal options
  - LLM model modal sections

### Telemetry app-side source refs

- `dc6cd49 feat(settings): add telemetry consent UI foundation`
- `d729258 feat(runtime): send consented translation telemetry`
- `bb091c7 fix(telemetry): sync settings view after consent dialog choice`
- `1a03a04 feat(debug-preview): add telemetry consent modal action`
- `55f0d62 fix(ui): update telemetry consent copy`
- `7c823fb fix(ui): show telemetry setting modal`
- dev `src/puripuly_heart/core/telemetry.py`
- dev `src/puripuly_heart/ui/components/telemetry_consent_dialog.py`
- dev `src/puripuly_heart/ui/views/settings.py` telemetry card behavior

### Current vnext gap

- vnext has some fallback compatibility/migration concepts, but not the latest dev curated fallback UX/defaults as a whole.
- vnext does not have app-side telemetry consent/settings/runtime shell.
- vnext broker telemetry endpoint/migration is also absent, but broker implementation is explicitly out of this bundle.

## 4. In scope

### C1. Settings model and compatibility

- Define vnext-canonical fallback intent representation.
- Accept/migrate legacy fallback keys from older settings.
- Add telemetry consent and operational state in the correct settings/state boundary.
- Keep `to_dict`/`from_dict` and `settings_vnext` migration/serialization synchronized.

### C2. Curated fallback runtime/provider/UI

- Implement curated fallback presets in vnext runtime/provider wiring.
- Implement first-run fallback defaults.
- Update fallback modal and LLM modal sections.
- Hide legacy DeepSeek China fallback option from new UI while preserving load compatibility.

### C3. App-side telemetry consent/runtime shell

- Add consent dialog and settings card.
- Add app-side runtime service that sends at most one translation success day signal per UTC date when consented.
- Add adapter port/interface only; do not implement broker endpoint/migration/API in this bundle.
- Add debug preview action with no side effects.

### C4. Integration cleanup

- i18n parity.
- settings migration fixtures/tests.
- runtime resolution/provider wiring tests.
- settings view/app/controller tests.

## 5. Out of scope

The following are forbidden in this bundle:

- broker telemetry endpoint, migration, aggregate/report, broker tests
- broker AGPL source offer endpoint/header
- README/setup guide/screenshots/release notes
- release version bump
- dashboard manual typing indicator
- QQ auth copy/key modernization
- STT session-open serialization
- DeepSeek/Cerebras non-200 provider error extraction
- Rust/native overlay changes
- legacy headless runtime restoration

## 6. Architecture boundaries

This bundle changes:

- settings/persistence
  - fallback user intent
  - telemetry consent user intent
  - telemetry operational state
- runtime resolution
  - fallback resolved runtime config
  - telemetry runtime adapter config if needed
- adapter wiring
  - fallback provider wiring
  - telemetry client port
- lifecycle owner
  - non-blocking telemetry send path ownership if async task is needed
- UI rendering
  - settings view fallback modal
  - LLM model modal sections
  - telemetry consent dialog/settings card
- message/diagnostics
  - telemetry safe diagnostics
  - fallback UI helper messages
- i18n
  - fallback/telemetry keys in all locale bundles

Mandatory separation rule:

- Persisted user intent, persisted operational state, resolved runtime config, and runtime-only state must remain separate.

## 7. Detailed requirements: C1 Settings model and compatibility

### C1.1 Fallback canonical model

vnext should not blindly resurrect dev legacy structures if vnext has a better canonical intent model. The implementation must decide and document in code/tests which representation is canonical.

Required fallback choices at the product level:

```text
none
deepseek_v4_flash_official
openrouter_deepseek_v4_flash
openrouter_gemma4_26b_a4b
cerebras_gemma4_31b
```

The exact enum/class names may follow vnext style, but persisted values must be stable and migration-safe.

### C1.2 Legacy fallback compatibility inputs

The loader/migration path must tolerate old data from dev and earlier vnext experiments, including at least:

- `openrouter.fallback_selection_alias`
  - `none`
  - `qwen35_flash`
  - `deepseek_v4_flash`
  - `deepseek_v4_flash_china`
  - old Gemini/Gemma fallback aliases where supported by current code
- `translation.fallback_selection_alias`
  - `none`
  - `deepseek_v4_flash_official`
  - `openrouter_deepseek_v4_flash`
  - `openrouter_gemma4_26b_a4b`
  - `cerebras_gemma4_31b`
- old fallback representation if vnext currently serializes compatibility fields under `intent.translation`.

Compatibility behavior:

- Unknown fallback alias must fall back safely to `none` or the current safe default.
- Legacy DeepSeek China fallback must remain loadable but should not be offered in new UI.
- Migration/serialization should avoid re-emitting deprecated compatibility-only fields unless vnext intentionally keeps a legacy surface.

### C1.3 Telemetry settings/state model

Telemetry must separate user intent from operational state.

Persisted user intent:

```text
telemetry consent: unknown | allow | decline
```

Persisted operational state:

```text
anonymous telemetry identifier, nullable
sent UTC dates, list of yyyy-mm-dd strings
```

Requirements:

- Existing settings without telemetry fields must load with safe defaults.
- Unknown consent must not send telemetry.
- Decline must clear identifier and sent dates.
- Allow must ensure an identifier exists.
- Malformed sent dates must be ignored/normalized safely.
- `to_dict`/`from_dict` and `settings_vnext` serialization/migration must stay synchronized.

## 8. Detailed requirements: C2 Curated fallback runtime/provider/UI

### C2.1 First-run defaults

Required product semantics from dev:

- Non-China first-run locale:
  - main managed/default provider remains vnext default unless vnext intentionally changed it
  - fallback default should be OpenRouter DeepSeek V4 Flash
- China first-run locale:
  - Managed China defaults remain selected
  - DeepSeek-only provider routing remains for Managed China main path
  - fallback default should preserve the dev semantic of using OpenRouter Gemma 4 26B A4B as separate fallback intent

If vnext architecture makes one of these defaults unsafe or obsolete, the implementation must record the decision in tests and implementation notes rather than silently dropping it.

### C2.2 Runtime resolution

Resolved fallback config should be derived from canonical fallback intent.

Requirements:

- `none` resolves to no fallback provider.
- `deepseek_v4_flash_official` resolves to official DeepSeek BYOK/credential path.
- `openrouter_deepseek_v4_flash` resolves to OpenRouter DeepSeek V4 Flash path.
- `openrouter_gemma4_26b_a4b` resolves to OpenRouter Gemma 4 26B A4B path.
- `cerebras_gemma4_31b` resolves to official Cerebras BYOK path.
- Fallback resolution must not mutate persisted settings at runtime.
- Credential source and provider routing must be explicit in resolved DTO/config.

### C2.3 Provider wiring

Provider factory/wiring must consume resolved fallback config, not raw UI settings where possible.

Requirements:

- Maintain async provider calls and close lifecycle.
- Do not introduce convenient imports across architecture boundaries.
- If fallback racing uses provider list/candidates, candidates should be built from resolved DTOs or service ports.
- Existing OpenRouter routing updates must remain intact.
- Existing Cerebras provider wiring must remain intact.

### C2.4 Settings UI fallback modal

Curated options shown to users:

```text
none
DeepSeek V4 Flash official
OpenRouter DeepSeek V4 Flash
OpenRouter Gemma 4 26B A4B
Cerebras Gemma 4 31B
```

UI requirements:

- Do not show legacy DeepSeek China fallback option in the new modal.
- Show description only where intended.
- Cerebras Gemma 4 31B should have a description explaining official BYOK/free tier suitability, matching updated copy semantics.
- If old settings contain a hidden legacy fallback, UI should display a safe current value, not a broken selection.
- OpenRouter key card visibility must follow actual credential needs and not expose irrelevant key input for inactive fallback paths.

### C2.5 LLM model modal sections

The LLM translation model modal should group options:

Recommended:

```text
Gemma 4
DeepSeek V4 Flash
```

Others:

```text
Cerebras Gemma 4 31B
Local LLM
DeepSeek V4 Pro
Gemini 3 Flash
Gemini 3.1 Flash Lite
Qwen 3.5 Plus
```

Names should follow current vnext model names if they differ slightly. The section semantics are what matter.

## 9. Detailed requirements: C3 App-side telemetry consent/runtime shell

### C3.1 Broker exclusion

This bundle must not implement:

- `broker/migrations/0011_add_telemetry_active_days.sql`
- `broker/src/telemetry.ts`
- `POST /v1/telemetry/translation-success-day`
- broker usage aggregates
- broker telemetry tests

Only app-side shell and adapter port are in scope.

### C3.2 Consent dialog

Required behavior:

- If telemetry consent is `unknown`, show consent dialog at the appropriate first-run/startup boundary.
- Allow sets consent to `allow` and ensures identifier exists.
- Decline sets consent to `decline` and clears identifier/sent dates.
- Choice updates settings view if it is mounted.
- Dialog copy must explain minimal anonymous success-day signal and what is not sent.

Do not send telemetry from the dialog itself.

### C3.3 Settings card

Required behavior:

- Settings general tab includes telemetry card.
- Card shows on/off state.
- User can open modal to switch allow/decline.
- Switching off clears identifier and sent dates.
- Switching on creates a new identifier if absent.
- Settings updates must go through vnext settings mutation/persistence path.

### C3.4 Debug preview

Required behavior:

- Debug preview panel includes telemetry consent modal action only when explicit debug preview mode is enabled.
- Preview action opens a dialog preview but must not:
  - persist settings
  - mutate secrets
  - call network/broker
  - generate/replace actual telemetry identifier

### C3.5 Runtime telemetry service

Required behavior:

- On successful translation, the app may attempt to record a translation success day only if consent is `allow` and identifier exists.
- If consent is `unknown` or `decline`, do nothing and return a no-op result.
- If the current UTC date is already in sent dates, do nothing.
- If send succeeds, persist current UTC date in sent dates.
- If send fails, do not block translation and do not mark the date as sent.
- Failure diagnostics must be safe and bounded.

### C3.6 Telemetry client port

Define an app-side port/protocol for telemetry sending.

Example semantic contract:

```text
record_translation_success_day(identifier, active_date_utc) -> success/failure
```

Implementation details:

- The actual broker endpoint adapter can be a minimal placeholder/no-op or existing broker client wrapper if available, but broker API implementation is out of scope.
- Runtime service must depend on the port, not directly on broker implementation details.
- Network calls must be async.

## 10. I18n requirements

All new user-facing text must go through i18n keys.

Affected copy areas:

- fallback labels/descriptions
- fallback modal title/helper
- LLM modal section names
- telemetry consent dialog title/body/actions
- telemetry settings card labels/descriptions
- debug preview telemetry action

Locale bundles to update:

```text
src/puripuly_heart/data/i18n/en.json
src/puripuly_heart/data/i18n/ko.json
src/puripuly_heart/data/i18n/ja.json
src/puripuly_heart/data/i18n/zh-CN.json
```

Locale parity is mandatory.

## 11. Likely touched files

Expected, not mandatory:

```text
src/puripuly_heart/config/settings.py
src/puripuly_heart/config/settings_vnext/schema.py
src/puripuly_heart/config/settings_vnext/migration.py
src/puripuly_heart/config/settings_vnext/serialization.py
src/puripuly_heart/config/runtime_resolution.py
src/puripuly_heart/config/llm_profiles.py

src/puripuly_heart/app/wiring.py
src/puripuly_heart/app/wiring_llm_factory.py

src/puripuly_heart/core/telemetry.py
src/puripuly_heart/core/llm/fallback_racing.py

src/puripuly_heart/ui/views/settings.py
src/puripuly_heart/ui/app.py
src/puripuly_heart/ui/controller.py
src/puripuly_heart/ui/components/debug_preview_panel.py
src/puripuly_heart/ui/components/telemetry_consent_dialog.py

src/puripuly_heart/data/i18n/en.json
src/puripuly_heart/data/i18n/ko.json
src/puripuly_heart/data/i18n/ja.json
src/puripuly_heart/data/i18n/zh-CN.json

tests/config/test_config_and_secrets.py
tests/config/test_first_run_locale.py
tests/config/test_runtime_resolution.py
tests/config/test_settings_vnext_migration_serialization.py
tests/config/test_settings_vnext_schema.py
tests/app/test_wiring_providers.py
tests/core/test_telemetry.py
tests/ui/test_settings_view_branches.py
tests/ui/test_app_branches.py
tests/ui/test_debug_preview_panel.py
tests/ui/test_i18n_key_usage.py
```

Avoid touching:

```text
broker/*
README*.md
installer.iss
native/overlay/Cargo.toml
src/puripuly_heart/providers/llm/deepseek.py
src/puripuly_heart/providers/llm/cerebras.py
src/puripuly_heart/core/stt/controller.py
```

## 12. Internal phase plan

Bundle C should be decomposed internally into 4 phases.

### Phase C1: settings model and compatibility

- Add/adjust fallback intent schema.
- Add telemetry consent/operational state schema.
- Implement migration/serialization compatibility.
- Add settings roundtrip tests before UI work.

### Phase C2: fallback runtime/provider/UI

- Resolve fallback intent into runtime DTO/config.
- Wire fallback providers from resolved config.
- Update settings UI fallback modal.
- Update LLM modal sections.
- Add fallback provider/runtime/UI tests.

### Phase C3: telemetry consent/UI/runtime shell

- Add telemetry consent dialog and settings card.
- Add debug preview no-side-effect action.
- Add telemetry runtime service and client port.
- Add telemetry tests.

### Phase C4: integration cleanup

- I18n parity.
- Settings fixture/snapshot updates.
- Full targeted test pass.
- Resolve conflicts from Bundles A/B if this bundle rebases last.

## 13. Verification plan

Use project virtual environment when available.

Targeted tests:

```powershell
.venv\Scripts\python -m pytest tests/config/test_config_and_secrets.py tests/config/test_first_run_locale.py tests/config/test_runtime_resolution.py tests/config/test_settings_vnext_migration_serialization.py tests/config/test_settings_vnext_schema.py tests/app/test_wiring_providers.py tests/core/test_telemetry.py tests/ui/test_settings_view_branches.py tests/ui/test_app_branches.py tests/ui/test_debug_preview_panel.py tests/ui/test_i18n_key_usage.py
```

If provider wiring changes touch integration helpers, run relevant provider/fallback integration tests available in vnext.

## 14. Acceptance criteria

### Fallback acceptance

- Existing settings without new fallback fields load successfully.
- Legacy fallback aliases migrate safely.
- `to_dict`/`from_dict` and `settings_vnext` serialization remain synchronized.
- First-run fallback defaults match required semantics or have explicit documented test-backed deviation.
- Runtime resolution produces explicit fallback provider/credential config.
- Provider wiring uses resolved config and preserves existing OpenRouter/Cerebras behavior.
- Fallback modal shows only curated options.
- Legacy DeepSeek China fallback is hidden in UI but remains loadable.
- LLM modal has recommended/others sections.
- All fallback copy is localized.

### Telemetry acceptance

- Existing settings without telemetry fields load with consent `unknown` and no identifier.
- Allow creates/keeps identifier.
- Decline clears identifier and sent dates.
- Unknown/decline never calls telemetry client.
- Already-sent date skips network.
- Successful send persists date.
- Failed send does not block translation and does not persist date.
- Consent dialog updates settings view after choice.
- Debug preview telemetry action has no persistence/secret/network side effects.
- All telemetry copy is localized.

### Scope acceptance

- No broker implementation changes.
- No README/release bump.
- No dashboard manual typing or QQ copy changes beyond conflict resolution preserving Bundle B.
- No provider non-200 error work beyond conflict resolution preserving Bundle A.

## 15. Merge and conflict notes

Bundle C should merge last.

Expected conflict hotspots with Bundle B:

- `src/puripuly_heart/ui/app.py`
- `src/puripuly_heart/ui/controller.py`
- `src/puripuly_heart/data/i18n/*.json`
- `tests/ui/test_i18n_key_usage.py`
- `tests/ui/test_app_branches.py`
- `tests/ui/test_settings_view_branches.py`

Conflict resolution principle:

- Keep Bundle B dashboard manual typing and QQ auth copy.
- Keep Bundle C fallback/telemetry settings and UI.
- In i18n files, preserve union of keys and locale parity.
- In app/controller wiring, preserve all independently owned callbacks/dialogs/tasks.

Expected conflict hotspots with Bundle A:

- Provider wiring only if fallback provider construction overlaps with provider error detail changes.
- `core/orchestrator/hub.py` only if telemetry success hook overlaps with latency diagnostics.

Conflict resolution principle:

- Keep Bundle A safe diagnostics/provider error handling.
- Add telemetry hook without weakening diagnostics or blocking translation.

## 16. Decomposition notes for `/decompose-docs`

When turning this spec into execution contracts, split into these subcontracts:

1. Settings compatibility and schema contract
2. Fallback runtime resolution/provider wiring contract
3. Fallback settings UI/i18n contract
4. Telemetry consent settings/UI contract
5. Telemetry runtime service/client port contract
6. Debug preview no-side-effect contract
7. Integration and conflict cleanup contract

Each subcontract must retain shared out-of-scope rules: no broker, no README/release, no manual typing, no QQ copy modernization, no STT session serialization, no provider non-200 error extraction.
