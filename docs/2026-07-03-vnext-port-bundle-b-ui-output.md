# Bundle B Spec: vnext UI Output Routing & QQ Auth Copy

작성일: 2026-07-03
대상 기준선: `vnext`
비교 기준: `dev` vs `vnext`, merge-base `2391e4877860a75c9bbc847074ae813a9467414e`
예상 작업 브랜치: `work/vnext-port-ui-output`
권장 worktree: `puripuly_heart-wt-ui-output`

## 1. Goal

`dev`에만 남은 UI/output 관련 작은 변경을 `vnext` 구조에 맞춰 포팅한다.

이 번들은 다음 2개 변경을 묶는다.

1. Dashboard manual input typing → VRChat typing indicator
2. QQ managed auth 최신 copy/key 흐름

핵심 목표는 **UI rendering, output routing, i18n/message boundary**를 정리하는 것이다. settings schema, fallback, telemetry, STT runtime, provider error handling은 이 번들에서 다루지 않는다.

## 2. Why this is a separate bundle

Manual typing은 UI input event를 output routing/OSC typing state로 연결한다. QQ copy modernization은 UI dialog/message/i18n을 바꾼다. 둘 다 UI-facing 변경이며 `ui/app.py`, `ui/controller.py`, i18n bundle에서 충돌할 수 있으므로 같은 worktree에서 처리한다.

반대로 Bundle A의 core runtime/provider 변경과 Bundle C의 settings-heavy 변경과는 분리한다.

## 3. Source refs and evidence

### Manual typing source refs

- `a118dde feat(ui): show VRChat typing for dashboard input`
- dev `src/puripuly_heart/core/osc/chatbox_paginator.py`
  - reason-based typing state
- dev `src/puripuly_heart/ui/components/display_card.py`
  - input activity callback
- dev `src/puripuly_heart/ui/views/dashboard.py`
  - dashboard-level message input activity callback
- dev `src/puripuly_heart/ui/controller.py`
  - manual typing idle task and submit reason management
- dev `tests/ui/test_controller_manual_typing.py`
- dev `tests/ui/test_display_card.py`

### QQ copy source refs

- `5a35904 fix(i18n): update managed auth modal copy`
- dev i18n keys under `qq_auth.*`
- dev QQ auth dialog tests
- dev app/controller mapping for `qq_auth.error.key_unavailable`

### Current vnext gap

- vnext has QQ managed auth core flow, broker issuance clients, claim guard, dialog shell, and debug preview states.
- vnext QQ copy still uses older `qq_managed_auth.*` semantics.
- vnext has `ChatboxPaginator.send_typing(bool)` but lacks dashboard manual input activity ownership and reason-based typing state.
- vnext does not propagate `DisplayCard` input activity to controller-owned VRChat typing state.

## 4. In scope

### B1. Dashboard manual typing indicator

- Track whether the dashboard manual message input has non-empty text.
- Show VRChat typing indicator while the user is actively typing.
- Clear typing on empty input, focus lost, idle timeout, submit completion, or shutdown.
- Preserve peer utterance chatbox hard-deny.

### B2. QQ auth copy/key modernization

- Update QQ managed auth dialog copy to the newer `qq_auth.*` semantics.
- Include free usage, QQ group number, activation credential, QQ number wording.
- Update recoverable key unavailable message behavior/copy.
- Update QQ/Discord mutual claim conflict copy.

## 5. Out of scope

The following are forbidden in this bundle:

- settings schema changes
- `settings_vnext` migration/serialization changes
- fallback preset/default/runtime changes
- telemetry consent/settings/runtime changes
- STT session open serialization
- DeepSeek/Cerebras provider non-200 error handling
- broker changes
- README/release bump
- Rust/native overlay changes

## 6. Architecture boundaries

This bundle changes:

- UI rendering
  - DisplayCard input activity
  - DashboardView callback wiring
  - QQ auth dialog text/actions
- output routing
  - self/manual typing state to VRChat typing channel
  - reason-based typing state or equivalent output port semantics
- lifecycle owner
  - manual typing idle task ownership/cancellation
- message/diagnostics
  - QQ recoverable/error message keys
- i18n
  - `qq_auth.*` keys and locale parity

This bundle must preserve:

- self, peer, and system output separation
- peer utterance hard-deny from VRChat chatbox
- debug preview no-side-effect behavior

## 7. Detailed requirements

### B1. Dashboard manual typing indicator

#### Required behavior

- When dashboard manual input contains non-whitespace text, set VRChat typing active for reason `manual_input` or equivalent.
- When input is empty or whitespace-only, clear that reason.
- When input focus is lost, clear that reason.
- When a message is submitted, clear manual input typing reason and set a submit-specific typing reason while submit is being processed.
- When submit processing finishes or fails, clear the submit typing reason.
- If multiple reasons are active, typing remains visible until all reasons are cleared.
- Idle timeout clears manual input typing after no activity.
- Shutdown/release clears all manual typing state and cancels idle task.

#### Privacy requirement

- `DisplayCard` must not expose the typed text to outer layers for typing-state purposes.
- It may emit only `bool has_text` or an equivalent non-content signal.

#### Output routing requirement

- Manual input is a self/manual channel signal.
- Peer utterances must not route to VRChat chatbox or typing indicator.
- Existing chatbox hard-deny tests must remain valid.

#### Implementation guidance

- `DisplayCard` can accept `on_input_activity: Callable[[bool], object] | None` or vnext-equivalent.
- `DashboardView` can expose `on_message_input_activity` to app/controller wiring.
- The controller or an explicit lifecycle owner should own manual typing state.
- Prefer reason-based API at the output boundary:
  - `set_typing_reason(reason: str, active: bool)`
  - `clear_typing_reasons()`
- If vnext has a more explicit output/message port, implement reason semantics there instead of adding convenient imports.
- In Flet callbacks, use `page.run_task` for async work.
- Avoid unmanaged long-lived `asyncio.create_task`; owner-scoped tasks must be cancelled on shutdown.

#### Suggested reason names

These are examples, not mandatory if vnext uses typed constants:

```text
manual_input
manual_submit:<generation>
```

Generation-based submit reasons prevent stale submit completions from clearing newer submit state.

### B2. QQ auth copy/key modernization

#### Required behavior

- Replace old user-facing QQ managed auth copy with newer copy semantics.
- Copy should describe:
  - PuriPuly free usage for new users
  - 700+ translations or equivalent free allowance wording from dev copy
  - QQ group number `647594597`
  - activation credential from group bot
  - QQ number input
- Dialog action labels should follow newer `qq_auth.*` naming.
- `key_unavailable` should remain a recoverable completion state.
- If key issuance is unavailable after accepted QQ verification, translation must remain off until a managed key can be issued.
- QQ/Discord mutual claim conflict messages should explain that the other method is no longer available after one method has claimed a Managed key.

#### Key naming guidance

Prefer modern keys from dev:

```text
qq_auth.body
qq_auth.submit
qq_auth.close
qq_auth.cancel
qq_auth.waiting_body
qq_auth.success
qq_auth.qq_identity.label
qq_auth.qq_identity.helper
qq_auth.credential.label
qq_auth.credential.helper
qq_auth.error.invalid_input
qq_auth.error.credential_mismatch
qq_auth.error.lifetime_used
qq_auth.error.already_claimed_discord
qq_auth.error.retry
qq_auth.error.key_unavailable
```

If old `qq_managed_auth.*` keys are still referenced by vnext tests or debug preview code, either update all references or keep temporary alias keys. Do not leave broken key lookups.

#### Locale requirements

- Update all locale bundles:
  - `en`
  - `ko`
  - `ja`
  - `zh-CN`
- Keep i18n key parity.
- Non-Chinese locale copy should localize the QQ group number phrase rather than leaving Chinese-only group wording.
- Avoid privacy promises not supported by the flow.

#### Core flow constraint

- Do not reimplement QQ managed auth service, claim guard, or broker issuance.
- vnext already has core QQ managed auth flow.
- This bundle changes dialog copy, message mapping, and UI completion handling only.

## 8. Likely touched files

Expected, not mandatory:

```text
src/puripuly_heart/core/osc/chatbox_paginator.py
src/puripuly_heart/ui/components/display_card.py
src/puripuly_heart/ui/views/dashboard.py
src/puripuly_heart/ui/controller.py
src/puripuly_heart/ui/app.py
src/puripuly_heart/ui/components/qq_managed_auth_dialog.py
src/puripuly_heart/ui/components/debug_preview_panel.py

src/puripuly_heart/data/i18n/en.json
src/puripuly_heart/data/i18n/ko.json
src/puripuly_heart/data/i18n/ja.json
src/puripuly_heart/data/i18n/zh-CN.json

tests/core/test_chatbox_paginator.py
tests/ui/test_display_card.py
tests/ui/test_dashboard_view_branches.py
tests/ui/test_controller_manual_typing.py
tests/ui/test_qq_managed_auth_dialog.py
tests/ui/test_app_branches.py
tests/ui/test_i18n_key_usage.py
```

Avoid touching:

```text
src/puripuly_heart/config/settings.py
src/puripuly_heart/config/settings_vnext/*
src/puripuly_heart/config/runtime_resolution.py
src/puripuly_heart/providers/llm/deepseek.py
src/puripuly_heart/providers/llm/cerebras.py
broker/*
README*.md
```

## 9. Verification plan

Use project virtual environment when available.

Targeted tests:

```powershell
.venv\Scripts\python -m pytest tests/core/test_chatbox_paginator.py tests/ui/test_display_card.py tests/ui/test_dashboard_view_branches.py tests/ui/test_controller_manual_typing.py tests/ui/test_qq_managed_auth_dialog.py tests/ui/test_app_branches.py tests/ui/test_i18n_key_usage.py
```

If output routing/hard-deny contracts are represented elsewhere in vnext, also run those tests.

## 10. Acceptance criteria

- DisplayCard reports input activity without exposing text.
- Dashboard focus lost and empty input clear typing state.
- Idle timeout clears manual typing state.
- Submit path keeps a submit typing reason only while needed and clears it reliably.
- Multiple typing reasons preserve typing until all clear.
- Peer utterance hard-deny remains enforced.
- QQ auth dialog uses latest copy semantics and all required keys are localized.
- QQ key unavailable state is recoverable and does not enable translation.
- Debug preview controls remain no-side-effect.
- No settings/fallback/telemetry/broker/release files are changed.

## 11. Merge and conflict notes

Expected conflict hotspots with Bundle C:

- `src/puripuly_heart/ui/app.py`
- `src/puripuly_heart/ui/controller.py`
- `src/puripuly_heart/data/i18n/*.json`
- `tests/ui/test_i18n_key_usage.py`

Conflict resolution principle:

- Bundle B owns dashboard manual typing and QQ auth copy.
- Bundle C owns fallback and telemetry settings/UI.
- In i18n files, keep the union of keys from both bundles and preserve locale parity.

Recommended merge order:

1. Bundle A
2. Bundle B
3. Bundle C last, so settings/i18n conflicts can be resolved after B lands

## 12. Decomposition notes for `/decompose-docs`

When turning this spec into execution contracts, split into these subcontracts:

1. Manual typing output routing contract
2. Manual typing lifecycle/shutdown verification contract
3. QQ auth copy/key modernization contract
4. I18n parity and debug preview no-side-effect contract

Each subcontract must retain the shared out-of-scope rules: no settings schema, no fallback, no telemetry, no provider error handling, no STT controller serialization, no broker.
