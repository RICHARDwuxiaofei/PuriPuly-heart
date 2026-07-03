# vnext 리팩터링 포팅 대상 정리

작성일: 2026-07-03
기준 비교: `dev` vs `vnext`, merge-base `2391e4877860a75c9bbc847074ae813a9467414e`

## 목적

`vnext`가 최종적으로 `main`/`dev` 역할을 이어받는다는 전제에서, `dev`에만 남아 있는 작업 중 **vnext 아키텍처에 맞춰 리팩터링해서 포팅해야 하는 앱 코드/런타임/UI 작업**만 남긴다.

단순 문서, 릴리스 번호, broker 단독 변경처럼 vnext 내부 형식으로 이식할 필요가 없는 항목은 이 문서의 구현 대상에서 제외한다.

## 명시적 제외 범위

아래 항목은 이 문서의 리팩터링 포팅 대상이 아니다.

- README, localized README, screenshot, setup guide, release-note 성격 문서
- broker 단독 변경
  - AGPL `/source`/`Link rel="source"` source-offer endpoint/header
  - telemetry ingest/aggregate/migration/API/broker tests
  - broker deploy smoke 또는 broker README
- 릴리스 버전 bump (`2.1.2` → `2.2.0`)와 installer/native version bump
- `AGENTS.md` 운영 규칙 변경
- vnext에서 의도적으로 제거한 legacy headless runtime 복구
- 단순 cosmetic UI tweak
  - 예: language modal section header `12` → `18`
- 대량 mechanical diff 중 vnext architecture 자체로 대체된 app service/port/runtime owner 파일 이동

## 포팅 대상 요약

| 우선순위 | 대상 | 변경 경계 | vnext 상태 | source ref |
| --- | --- | --- | --- | --- |
| P0 | Dashboard manual input typing → VRChat typing indicator | UI rendering, output routing, lifecycle owner | 없음 | `a118dde` |
| P0 | STT session-open serialization | lifecycle owner, runtime resolution/STT service | 없음 | dev `_session_open_lock` |
| P1 | STT/latency diagnostics noise reduction | message/diagnostics | 일부 없음 | `1b2a769` |
| P1 | DeepSeek/Cerebras non-200 error detail extraction | adapter wiring, message/diagnostics | 일부 없음 | dev provider diff |
| P1 | curated fallback presets/defaults/UX 최신화 | settings/persistence, runtime resolution, UI rendering | 부분 적용 | `1b3e484`, `c72ea2c`, `33e89cb`, `2052d48`, `0660c31` |
| P2 | QQ managed auth 최신 copy/key 흐름 | UI rendering, message/diagnostics | 핵심 기능은 있음, copy/key는 구형 | `5a35904` |
| P2 | app-side telemetry consent/runtime shell | settings/persistence, runtime resolution, UI rendering, adapter wiring | 없음 | `dc6cd49`, `d729258`, `bb091c7`, `1a03a04`, `55f0d62`, `7c823fb` |

## 1. Dashboard manual input typing → VRChat typing indicator

Source ref: `a118dde feat(ui): show VRChat typing for dashboard input`

### 왜 vnext 리팩터링 포팅이 필요한가

dev 구현은 dashboard text input activity를 OSC typing state로 연결한다. vnext는 output routing과 lifecycle owner 분리가 진행되어 있으므로, UI에서 직접 OSC sender semantics를 가정하지 말고 vnext output/chatbox routing 경계에 맞춰야 한다.

### 필요한 동작

- dashboard input에 non-empty text가 있으면 VRChat typing indicator를 켠다.
- input이 비거나 focus를 잃으면 typing indicator를 끈다.
- submit 중에는 짧게 submit typing reason을 유지한 뒤 해제한다.
- idle timeout으로 typing state가 자동 해제된다.
- 여러 typing reason이 겹쳐도 하나가 남아 있으면 indicator가 유지된다.
- peer utterance는 여전히 VRChat chatbox/typing route로 보내지 않는다.

### vnext 포팅 지침

- `DisplayCard`/`DashboardView`는 text 내용을 외부로 노출하지 않고 boolean activity만 emit한다.
- `GuiController` 또는 vnext lifecycle owner가 idle task를 소유하고 shutdown/cancel 경로를 가진다.
- `ChatboxPaginator`에는 단일 `send_typing(bool)`보다 reason 기반 API가 필요하다.
  - 예: `set_typing_reason(reason, active)` 또는 동등한 vnext output port.
- Flet callback에서는 async 작업을 `page.run_task` 경유로 시작한다.
- output routing 규칙상 peer/system channel과 self/manual channel을 분리한다.

### 검증 후보

- dashboard input activity가 text value를 외부로 전달하지 않는 단위 테스트
- focus lost/empty input/idle timeout/submit completion에서 typing reason이 해제되는 테스트
- output router/chatbox hard-deny 테스트가 계속 통과하는지 확인

## 2. STT session-open serialization

Source ref: dev `src/puripuly_heart/core/stt/controller.py`의 `_session_open_lock`, `tests/core/test_stt_controller.py::test_stt_controller_serializes_concurrent_session_open`

### 왜 vnext 리팩터링 포팅이 필요한가

vnext에는 provider/session lifecycle owner 경계가 있으므로, 단순 lock 복사보다 STT session open ownership 안에서 concurrent open을 직렬화해야 한다.

### 필요한 동작

- 동일 STT controller/session owner에서 concurrent session open이 동시에 provider connect를 수행하지 않는다.
- 첫 open이 완료되기 전 들어온 두 번째 open은 기존 상태를 안전하게 기다리거나 순차 처리한다.
- cancellation/close 중 lock이 유실되거나 deadlock을 만들지 않는다.

### vnext 포팅 지침

- STT controller가 아직 session lifecycle owner라면 controller 내부 lock으로 포팅한다.
- 별도 runtime owner가 session open을 소유한다면 owner 내부에 serialization contract를 둔다.
- lock 범위에는 provider open 상태 전환만 넣고, 장기 running loop 전체를 lock으로 감싸지 않는다.
- diagnostics는 raw exception text보다 safe diagnostic fact를 우선한다.

### 검증 후보

- concurrent open이 provider open call을 1회 또는 의도한 순서로만 수행하는 테스트
- cancellation 중 lock release 확인 테스트
- 기존 Deepgram/Soniox empty final ack 테스트 회귀 확인

## 3. STT/latency diagnostics noise reduction

Source ref: `1b2a769 fix(diagnostics): reduce detailed log noise`

### 왜 vnext 리팩터링 포팅이 필요한가

dev에는 latency dominant stage와 finalization lag diagnostics가 있다. vnext는 message/diagnostics 경계를 더 명시적으로 분리하므로, dev의 raw string logging을 그대로 복사하지 말고 vnext runtime logging/diagnostic contract로 옮겨야 한다.

### 필요한 동작

- detailed logging에서 전체 로그 노이즈를 줄이면서 원인 판단에 필요한 latency cause metric을 남긴다.
- STT pending finalization이 지연될 때 `[STT][FinalizationLag]`에 해당하는 safe diagnostic을 남긴다.
- self/peer/provider/utterance id/stage durations 같은 분석 가능한 fact를 제공한다.
- raw provider payload, transcript, credential, stack trace는 로그에 남기지 않는다.

### vnext 포팅 지침

- `compute_latency_dominant_stage`, `format_latency_cause_metric` semantics를 vnext runtime logging service에 맞게 재표현한다.
- user-facing message가 아니라 diagnostic/event fact로 취급한다.
- basic/detailed logging mode 차이를 유지한다.
- finalization lag threshold는 코드에서 현재 orchestrator/STT default를 확인한 뒤 설정한다.

### 검증 후보

- latency cause metric이 detailed mode에서만 또는 의도한 logging mode에서만 출력되는 테스트
- finalization lag diagnostic이 지연 pending finalization에서만 출력되는 테스트
- transcript/provider payload가 로그에 포함되지 않는 guard 테스트

## 4. DeepSeek/Cerebras non-200 error detail extraction

Source ref: dev `providers/llm/deepseek.py`, `providers/llm/cerebras.py`

### 왜 vnext 리팩터링 포팅이 필요한가

vnext에는 Cerebras provider wiring 자체는 있으나, dev의 non-200 error body extraction은 없다. provider adapter에서 safe detail만 뽑아 diagnostic/log에 남기는 방식으로 포팅해야 한다.

### 필요한 동작

- non-200 response에서 가능한 경우 아래 순서로 error message를 추출한다.
  - JSON top-level `message`
  - JSON `error.message`
  - JSON `error` string
  - response text 앞부분의 bounded preview
- 로그와 `RuntimeError`는 `status=... message=...` 수준의 bounded detail만 포함한다.
- secret/API key/request body/full provider payload는 노출하지 않는다.

### vnext 포팅 지침

- vnext의 `provider_failure_report`/diagnostic contract를 유지해야 한다면, error message를 safe field로 넣고 formatter가 redact policy를 지키게 한다.
- DeepSeek와 Cerebras provider의 behavior를 맞춘다.
- async client lifecycle은 변경하지 않는다.

### 검증 후보

- DeepSeek/Cerebras non-200 JSON error body test
- text-only non-200 body bounded preview test
- secret/request body가 log/exception에 들어가지 않는 test

## 5. Curated fallback presets/defaults/UX 최신화

Source refs:

- `1b3e484 feat(llm): add curated translation fallbacks`
- `c72ea2c fix(config): use new fallback defaults on first run`
- `33e89cb feat(ui): split LLM translation modal into recommended and others sections`
- `2052d48 refactor(llm): reorder fallback options, remove option descriptions, split modal title`
- `0660c31 fix(ui): hide DeepSeek China fallback option, add Cerebras Gemma description`

### 왜 vnext 리팩터링 포팅이 필요한가

vnext에는 fallback compatibility/migration의 일부가 있지만, dev의 최신 UX/default semantics는 legacy `settings.py` 중심이다. vnext의 intent schema/runtime resolver에 맞춰 다시 표현해야 한다.

### 필요한 동작

- first-run fallback defaults를 최신 dev semantics와 맞춘다.
  - non-China locale: OpenRouter DeepSeek V4 Flash fallback 기본값
  - China locale: Managed China 기본값과 별도 fallback 기본값 보존
- fallback modal은 curated fallback set만 노출한다.
  - none
  - DeepSeek V4 Flash official
  - OpenRouter DeepSeek V4 Flash
  - OpenRouter Gemma 4 26B A4B
  - Cerebras Gemma 4 31B
- legacy DeepSeek China fallback option은 새 UI에서 숨긴다.
- Cerebras fallback만 description copy를 표시한다.
- LLM model modal은 recommended/others section을 가진다.
  - recommended: Gemma 4, DeepSeek V4 Flash
  - others: Cerebras, Local LLM, DeepSeek Pro, Gemini, Qwen 등
- OpenRouter API key card visibility는 main provider/fallback credential source와 일관되게 유지한다.

### vnext 포팅 지침

- legacy `TranslationFallbackSelectionAlias`를 그대로 되살리기보다 vnext `settings_vnext` intent model에 맞는 value object/DTO로 표현한다.
- persisted user intent, operational state, resolved runtime config를 분리한다.
- old `openrouter.fallback_selection_alias`는 compatibility input으로만 받아들이고, vnext canonical storage로 migrate/serialize한다.
- `to_dict`/`from_dict` compatibility와 `settings_vnext` migration/serialization을 함께 맞춘다.
- 모든 UI copy는 i18n key로 추가하고 locale parity를 유지한다.

### 검증 후보

- first-run locale별 fallback default test
- old fallback alias migration/serialization roundtrip test
- fallback modal option list/description/hidden legacy option test
- LLM modal section test
- provider wiring fallback resolution test

## 6. QQ managed auth 최신 copy/key 흐름

Source ref: `5a35904 fix(i18n): update managed auth modal copy`

### 왜 vnext 리팩터링 포팅이 필요한가

vnext는 QQ managed auth 핵심 기능을 이미 포팅했다. 남은 차이는 UI copy/key naming과 completion/error message mapping이다. vnext UI boundary/i18n 방식으로 최신 copy를 반영해야 한다.

### 필요한 동작

- `qq_managed_auth.*` 구형 copy를 dev의 `qq_auth.*` copy semantics로 교체한다.
- copy는 free usage, QQ group number `647594597`, activation credential, QQ number 입력을 설명한다.
- key unavailable은 recoverable completion message로 표시하고 translation은 켜진 척하지 않는다.
- Discord/QQ mutual claim conflict copy를 최신 문구로 맞춘다.

### vnext 포팅 지침

- core auth claim guard와 broker issuance flow는 이미 vnext에 있으므로 재구현하지 않는다.
- UI component와 app/controller message mapping만 조정한다.
- message key rename 시 old key compatibility가 필요한지 결정한다.
- 모든 locale bundle과 i18n parity tests를 함께 갱신한다.

### 검증 후보

- QQ auth dialog body/action/error copy test
- key unavailable completion path test
- required runtime i18n keys parity test
- Discord-after-QQ, QQ-after-Discord conflict snackbar/message test

## 7. App-side telemetry consent/runtime shell

Source refs:

- `dc6cd49 feat(settings): add telemetry consent UI foundation`
- `d729258 feat(runtime): send consented translation telemetry`
- `bb091c7`, `1a03a04`, `55f0d62`, `7c823fb`

### 전제

broker telemetry endpoint/migration/aggregate는 이 문서 범위에서 제외한다. 다만 제품 방향상 telemetry 기능을 유지한다면, app-side consent/settings/runtime shell은 vnext settings architecture에 맞춰 따로 포팅해야 한다.

### 필요한 동작

- first-run 또는 unknown consent 상태에서 telemetry consent dialog를 표시한다.
- settings general tab에서 telemetry on/off 상태와 설명을 제공한다.
- allow 시 익명 identifier를 생성하고, decline 시 identifier와 sent date state를 제거한다.
- 하루에 한 번 translation success signal만 보내도록 local sent date state를 관리한다.
- telemetry가 off/unknown이면 외부 호출을 하지 않는다.

### vnext 포팅 지침

- consent는 persisted user intent로, identifier/sent dates는 persisted operational state로 분리한다.
- broker endpoint adapter는 port/interface 뒤로 숨기고, broker 구현 자체는 이 문서 범위 밖에 둔다.
- preview action은 settings/secrets/network를 mutate하지 않아야 한다.
- telemetry copy는 최소/익명/성공일 단위 signal만 설명하고 raw transcript/voice data를 보내지 않는다는 boundary를 명확히 한다.

### 검증 후보

- consent allow/decline settings migration/serialization test
- first-run dialog choice가 settings view와 동기화되는 test
- off/unknown 상태에서 network call이 없는 test
- already-sent date skip 및 successful send 후 sent date persist test
- debug preview no-side-effect test

## 포팅 순서 제안

1. P0 안정성/UX부터 처리한다.
   - STT session-open serialization
   - Dashboard manual typing indicator
2. P1 diagnostics/provider safe error detail을 처리한다.
   - latency/finalization diagnostics
   - DeepSeek/Cerebras non-200 detail extraction
3. P1/P2 설정 schema 영향이 큰 항목을 별도 작업으로 처리한다.
   - curated fallback presets/defaults/UX
   - app-side telemetry shell
4. QQ managed auth copy/key는 핵심 auth flow를 건드리지 않는 UI-only 작업으로 분리한다.

## 완료 기준

- 각 항목은 vnext boundary 이름을 PR/작업 설명에 명시한다.
- settings 변경 항목은 `to_dict`/`from_dict`, `settings_vnext` migration/serialization, runtime resolver를 함께 갱신한다.
- user-facing copy는 모든 locale bundle과 i18n parity test를 갱신한다.
- diagnostics/logging 변경은 raw secret, raw provider payload, raw transcript를 노출하지 않는다.
- Rust/native overlay를 건드린 작업이 있으면 전체 작업 마지막에 Windows overlay rebuild를 수행한다.
