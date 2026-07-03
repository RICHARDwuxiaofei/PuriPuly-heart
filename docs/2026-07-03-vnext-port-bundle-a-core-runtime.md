# Bundle A Spec: vnext Core Runtime Safety & Diagnostics

작성일: 2026-07-03
대상 기준선: `vnext`
비교 기준: `dev` vs `vnext`, merge-base `2391e4877860a75c9bbc847074ae813a9467414e`
예상 작업 브랜치: `work/vnext-port-core-runtime`
권장 worktree: `puripuly_heart-wt-core-runtime`

## 1. Goal

`dev`에만 남아 있는 작은 런타임 안정성/진단 개선을 `vnext` 아키텍처에 맞게 재구현한다.

이 번들은 다음 3개 변경을 하나의 작은 runtime-focused 작업 단위로 묶는다.

1. STT session open 직렬화
2. STT/latency diagnostics noise reduction
3. DeepSeek/Cerebras provider non-200 error detail extraction

핵심 목표는 **settings schema, UI settings, fallback, telemetry, broker를 건드리지 않고** core/runtime/provider 관측성과 안정성을 올리는 것이다.

## 2. Why this is a separate bundle

이 작업은 주로 `core/stt`, `core/orchestrator`, `core/runtime_logging`, `providers/llm` 경계에 머문다. 큰 settings migration이나 i18n 변경 없이 독립적으로 검증 가능하다.

병렬 세션 관점에서 Bundle B/C와의 충돌을 줄이기 위해 다음을 강제한다.

- UI/dashboard/QQ/i18n 파일은 건드리지 않는다.
- `settings.py`, `settings_vnext/*`, `runtime_resolution.py`의 schema/intent 구조 변경은 하지 않는다.
- broker/README/release bump는 하지 않는다.

## 3. Source refs and evidence

### Source refs

- dev `src/puripuly_heart/core/stt/controller.py`
  - `_session_open_lock`
- dev `tests/core/test_stt_controller.py`
  - `test_stt_controller_serializes_concurrent_session_open`
- `1b2a769 fix(diagnostics): reduce detailed log noise`
- dev `src/puripuly_heart/core/runtime_logging.py`
  - `compute_latency_dominant_stage`
  - `format_latency_cause_metric`
- dev `src/puripuly_heart/core/orchestrator/hub.py`
  - `[Metric] latency_cause` emission path
- dev `src/puripuly_heart/core/stt/controller.py`
  - `[STT][FinalizationLag]` emission path
- dev `src/puripuly_heart/providers/llm/deepseek.py`
- dev `src/puripuly_heart/providers/llm/cerebras.py`
  - `_extract_error_message`
  - non-200 `status=... message=...` logging/exception behavior

### Current vnext gap

- vnext에는 provider-level Deepgram/Soniox empty final ack는 이미 있다.
- vnext에는 STT session open serialization용 `_session_open_lock` equivalent가 없다.
- vnext에는 `compute_latency_dominant_stage`, `format_latency_cause_metric`, `[Metric] latency_cause`, `[STT][FinalizationLag]` 신호가 없다.
- vnext DeepSeek/Cerebras provider는 non-200에서 provider body message를 추출하지 않고, 대체로 `provider returned non-success status` 수준으로 축약한다.

## 4. In scope

### A1. STT session-open serialization

동일 STT controller/session lifecycle owner에서 concurrent open 요청이 동시에 provider session open/connect를 수행하지 않게 한다.

### A2. STT/latency diagnostics noise reduction

상세 로그 노이즈를 줄이면서 latency 원인과 STT finalization lag를 분석 가능한 safe diagnostic으로 남긴다.

### A3. DeepSeek/Cerebras non-200 error detail extraction

Provider non-200 응답에서 안전한 bounded error message를 추출해 log/exception에 반영한다.

## 5. Out of scope

다음은 이 번들에서 금지한다.

- settings schema 또는 persisted settings migration 변경
- fallback preset/default/UX 변경
- telemetry consent/settings/runtime 변경
- dashboard manual typing 변경
- QQ managed auth copy/key/i18n 변경
- README, broker, release version bump
- Rust/native overlay 변경
- legacy headless runtime 복구

## 6. Architecture boundaries

이 번들은 다음 경계를 변경한다.

- lifecycle owner
  - STT session open serialization
  - cancellation/open/close path 안전성
- message/diagnostics
  - latency cause metric
  - STT finalization lag diagnostic
  - provider non-200 safe error facts
- adapter wiring
  - DeepSeek/Cerebras HTTP adapter error handling

다음 경계는 변경하지 않는다.

- settings/persistence
- runtime resolution schema
- UI rendering
- output routing
- broker contract

## 7. Detailed requirements

### A1. STT session-open serialization

#### Required behavior

- 같은 STT controller/session owner에 concurrent open 요청이 들어오면 provider open/connect가 동시에 실행되지 않는다.
- 두 번째 요청은 첫 번째 open state transition이 끝날 때까지 기다리거나, vnext owner contract에 맞는 safe no-op/serialized path를 따른다.
- open 실패 시 lock이 반드시 해제된다.
- cancellation 중에도 lock이 영구 점유되지 않는다.
- close/shutdown path와 deadlock을 만들지 않는다.
- lock 범위는 session open/state transition에 한정한다. long-running streaming loop 전체를 lock으로 감싸면 안 된다.

#### Implementation guidance

- vnext에서 STT controller가 session lifecycle owner이면 controller 내부 lock을 둔다.
- vnext에서 별도 runtime owner가 provider session open을 소유한다면 owner 내부에 serialization contract를 둔다.
- lock은 `asyncio.Lock` 또는 vnext lifecycle owner가 이미 제공하는 equivalent를 사용한다.
- unmanaged background task를 추가하지 않는다.
- provider `close()`가 필요한 teardown path에서는 반드시 `await`한다.

#### Failure behavior

- open 중 exception이 발생하면 다음 open attempt가 막히지 않아야 한다.
- cancellation이 발생하면 partial session state를 안전하게 정리해야 한다.
- raw exception text를 user-facing message로 직접 노출하지 않는다.

### A2. STT/latency diagnostics noise reduction

#### Required behavior

- translation/STT turn latency에서 dominant stage를 계산한다.
- detailed/basic logging mode 정책에 맞춰 latency cause diagnostic을 남긴다.
- STT finalization pending 상태가 threshold 이상 지연되면 finalization lag diagnostic을 남긴다.
- diagnostic에는 분석 가능한 fact를 포함한다.
  - channel/self-or-peer
  - provider name
  - utterance id 또는 safe correlation id
  - pending duration
  - stage durations
  - dominant stage
- diagnostic에는 다음이 포함되면 안 된다.
  - raw transcript
  - raw provider payload
  - API key/credential/secret
  - unredacted stack trace

#### Implementation guidance

- dev의 helper 이름을 그대로 복사할 필요는 없다. vnext `runtime_logging` 또는 diagnostic service contract에 맞게 옮긴다.
- diagnostic string이 필요한 경우에도 formatter 입력은 safe facts 중심으로 구성한다.
- noisy per-frame/per-token logs를 늘리지 않는다.
- finalization lag threshold는 현재 vnext STT/orchestrator default와 테스트 기대치를 확인해서 정한다.
- logging mode가 있는 경우 basic/detailed 구분을 유지한다.

#### Expected diagnostic semantics

Dev signal examples:

- `[Metric] latency_cause ...`
- `[STT][FinalizationLag] ...`

vnext implementation may preserve these labels for test/debug continuity, or map them to vnext diagnostic event names. If labels change, tests should assert semantics rather than brittle full strings where possible.

### A3. DeepSeek/Cerebras non-200 error detail extraction

#### Required behavior

For non-200 responses in DeepSeek and Cerebras translation calls:

1. Try to parse JSON.
2. Extract safe error message in this order:
   - top-level `message` string
   - nested `error.message` string
   - `error` string
   - bounded response text preview
3. If no detail is available, use `unknown error` or an equivalent safe fallback.
4. Log an error containing status and bounded message.
5. Raise a `RuntimeError` or vnext-equivalent provider failure with status and bounded message.

#### Security requirements

- Do not log request body.
- Do not log API key or auth header.
- Do not log full provider response payload.
- Response text preview must be bounded.
- Error message must be safe to display in logs; if vnext has sanitizer/redaction utility, use it.

#### Consistency requirements

- DeepSeek and Cerebras should behave consistently.
- Existing success path and streaming/non-streaming semantics must remain unchanged.
- Provider `close()` lifecycle must remain async and awaited.

## 8. Likely touched files

Expected, not mandatory:

```text
src/puripuly_heart/core/stt/controller.py
src/puripuly_heart/core/runtime_logging.py
src/puripuly_heart/core/orchestrator/hub.py
src/puripuly_heart/providers/llm/deepseek.py
src/puripuly_heart/providers/llm/cerebras.py

tests/core/test_stt_controller.py
tests/core/test_hub_low_latency.py
tests/providers/test_deepseek_provider.py
tests/providers/test_cerebras_provider.py
```

Avoid touching:

```text
src/puripuly_heart/config/settings.py
src/puripuly_heart/config/settings_vnext/*
src/puripuly_heart/ui/*
src/puripuly_heart/data/i18n/*
broker/*
README*.md
```

## 9. Verification plan

Use project virtual environment when available.

Targeted tests:

```powershell
.venv\Scripts\python -m pytest tests/core/test_stt_controller.py tests/core/test_hub_low_latency.py tests/providers/test_deepseek_provider.py tests/providers/test_cerebras_provider.py
```

If the touched code affects orchestrator integration paths, also run:

```powershell
.venv\Scripts\python -m pytest tests/core/test_orchestrator_pipeline.py tests/core/test_hub_branch_coverage.py
```

If provider error diagnostics use shared redaction/diagnostic helpers, also run the relevant diagnostic/logging tests present in vnext.

## 10. Acceptance criteria

- Concurrent STT session open is serialized and covered by test.
- No STT open deadlock under failure/cancellation test scenarios.
- Latency cause diagnostic is emitted only in intended logging mode/path.
- Finalization lag diagnostic is emitted for delayed pending finalization and not for normal completion.
- DeepSeek/Cerebras non-200 tests verify JSON body and text body detail extraction.
- Tests verify no raw secret/provider payload/request body appears in logs or raised errors.
- No settings/i18n/broker/README/release files are changed.

## 11. Merge and conflict notes

This bundle should be merged before Bundle B and Bundle C when possible.

Expected conflict risk:

- Low with Bundle B, unless both modify `ui/controller.py` indirectly. Bundle A should avoid UI controller changes.
- Low/medium with Bundle C if both touch `core/orchestrator/hub.py` or provider wiring. Prefer Bundle A first so Bundle C can rebase on stabilized diagnostics/provider behavior.

## 12. Decomposition notes for `/decompose-docs`

When turning this spec into execution contracts, split into these subcontracts:

1. STT session open serialization contract
2. Latency/finalization diagnostics contract
3. DeepSeek/Cerebras non-200 safe error detail contract
4. Verification contract

Each subcontract must retain the shared out-of-scope rules: no settings, no i18n, no UI, no broker, no release bump.
