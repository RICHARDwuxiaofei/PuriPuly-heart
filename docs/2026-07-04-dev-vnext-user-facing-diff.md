# dev ↔ vnext 사용자 노출 차이점 전수조사

- **비교 대상**: `dev` (구/현재) → `vnext` (신)
- **조사 범위**: UI 컴포넌트, 뷰, i18n 4개 로케일(en/ko/ja/zh-CN), 프로바이더 정의, 컨트롤러 동작
- **조사일**: 2026-07-04
- **방향 표기**: 모든 항목은 **dev(구) → vnext(신)** 순서. i18n 키는 ko.json 기준, 로케일별 편차는 별도 표기.
- **조사 방법**: `git diff dev vnext` 기반 전수 비교 + 컨트롤러/앱 동작 분석 서브에이전트 병행

---

## A. 모달 / 다이얼로그 구조 변경

### 1. 텔레메트리 동의 모달 — 완전 재작성
- **구조**: dev는 `warm_document_dialog` 재사용(단락 3개 + 기본 버튼) → vnext는 전용 `AlertDialog`(제목 24pt, 본문 16pt, 프라이버시 15pt, `allow`/`decline` 버튼, glow/surface 스타일, 너비 560).
- **문구** (ko):
  - 제목: `안녕하세요.` → `PuriPuly 개선에 참여할까요?`
  - 본문: `이번 업데이트에서 텔레메트리 기능을 추가했습니다...MAU...` (긴 단락) → `허용하면 PuriPuly가 번역에 성공한 날 하루에 한 번만 익명 성공일 신호를 보냅니다.`
  - `telemetry.consent.excludes`("발전에 기여해주세요...") **삭제** → 대신 `telemetry.consent.privacy`("오디오, 전사문, 번역문, 언어쌍, 제공자 응답, 실패 정보, 개인 식별자는 보내지 않습니다.") **신규 추가**.
  - 버튼: `동의`/`거부` → `익명 신호 허용`/`괜찮아요`.
- **표시 타이밍**: dev는 `controller.start()` 내부에서(파이프라인 시작 전) → vnext는 `main_gui()`에서 `controller.start()` 완료 **후** 에 노출. (둘 다 consent가 `unknown`일 때만)
- **설정 동기화**: dev는 모달 저장만 → vnext는 설정 뷰의 `on_telemetry_consent_change`도 연결.
- **관련 파일**: `ui/components/telemetry_consent_dialog.py`, `ui/controller.py`, `ui/app.py`

### 2. QQ 인증 모달 활성화 인증서 폼 — 대폭 재작업
- **입력 검증 제거**: dev는 64자 헥스 정규식(`\A[0-9a-f]{64}\Z`)·QQ번호 길이·유니코드 카테고리 검증 → vnext는 **검증 없음**, 빈 값만 체크.
- **에러 표시 방식**: dev는 본문 전체를 에러 메시지로 교체 + 필드 재생성(`set_recoverable_error`) → vnext는 **인라인 에러 텍스트**(주황색, 18pt)를 필드 아래 표시(`set_error`), 필드는 그대로 두고 비활성화만.
- **비밀번호 토글**: vnext에 `can_reveal_password=True` 추가(👁 버튼 노출).
- **콜백**: `on_submit` → `on_continue`로 변경, `on_completion_message` 제거.
- **generation/pending 상태 제거**: dev의 `auth_generation`, `_pending_*`, `complete_success`/`complete_key_unavailable`/`complete_translation_enable_failed` 일련의 메서드 → vnext는 단순 `set_error`/`set_waiting`/`close` 3개로 축소.
- **간격**: `body_spacing` 44 → 28.
- **관련 파일**: `ui/components/qq_managed_auth_dialog.py`

### 3. 폴백 선택 모달 — 재작성
- **제목**: `번역 지연이 있을 때 호출할 모델을 정합니다.` → `폴백 선택`
- **옵션 라벨** (ko):
  - `DeepSeek V4 Flash (공식 API)` → `DeepSeek V4 Flash · 공식 BYOK`
  - `DeepSeek V4 Flash (OpenRouter)` → `DeepSeek V4 Flash · OpenRouter`
  - `Gemma 4 26B A4B (OpenRouter)` → `Gemma 4 26B · OpenRouter`
  - `Gemma 4 31B (Cerebras)` → `Cerebras Gemma 4 31B · 공식 BYOK`
- **도움말**:
  - `inactive_helper`: "지금 저장되며 기본 번역 요청이 실패하거나 2000ms...사용돼요" → "선택 사항인 폴백 번역 브랜치로 저장돼요."
  - `active_helper`: "기본 번역 요청이 실패하거나 2000ms...사용돼요" → "기본 번역 브랜치가 2000ms 폴백 기한을 넘기면 사용돼요." (**"실패" 트리거 제거**, 2000ms 초과만)
  - `none.description`: "...기본 번역 provider만..." → "...기본 번역 브랜치만..." (provider→branch 용어 변경)
- **삭제된 키**: `settings.fallback.legacy_openrouter_helper`, `settings.openrouter_fallback.inactive_helper`, `settings.openrouter_fallback.active_helper`.
- **Cerebras 폴백 설명**: `폴백 모델로 사용하기 좋아요. 무료 API 티어를 활용하세요` → `내 API 키로 Cerebras 공식 API를 사용해요. 매우 빠른 폴백이 필요하고 무료 티어 또는 유료 한도를 사용할 수 있을 때 적합해요.`
- **아키텍처**: 폴백 선택이 `TranslationFallbackSelectionAlias`(enum) → `TranslationFallbackSettings`(model+connection 조합)로 변경. legacy OpenRouter 폴백 매핑(`LEGACY_FALLBACK_ALIAS_TO_ALIAS`)이 빈 `{}`로 초기화.
- **관련 파일**: `ui/views/settings.py`, `config/llm_profiles.py`, `config/settings.py`

---

## B. 모델 / 프로바이더 옵션 변경

### 4. Cerebras Gemma 4 설명
- `settings.translation_model.gemma4_31b_cerebras.description`:
  - ko: `매우매우 빨라요 매우 비싸요` → `매우 빠르고 비용이 높은 공식 API 접속이에요`
  - en: `Very very fast Very expensive` → `Very fast and high-cost official API access`
  - zh: `非常非常快 非常贵` → `非常快且成本较高的官方 API 接入`

### 5. 언어 선택 카드 "최근" / "모든 언어"
- `language_modal.py`: 헤더 폰트 사이즈 **18 → 12**로 축소.
- `language_card.py`: 동작 변경 없음(주석 3줄 삭제만).

### 추가 발견 — 모델 설명 교체
- `deepseek_v4_pro.description`: `번역 속도가 불안정할 수 있어요` → **빈 문자열**(삭제)
- `gemini31_flash_lite.description`: **빈 문자열** → `번역 속도가 불안정할 수 있어요`(deepseek_v4_pro에서 이동)
- 즉, "번역 속도가 불안정할 수 있어요" 설명이 deepseek_v4_pro → gemini31_flash_lite로 이동.

### 추가 발견 — 프로바이더 라벨 키 삭제/이동
- **삭제**: `provider.gemini3_flash_openrouter`("Gemini 3 Flash (OpenRouter BYOK)"), `provider.gemini31_flash_lite_openrouter`("Gemini 3.1 Flash Lite (OpenRouter BYOK)"), `provider.qwen35_flash_fallback`.
- `provider.gemma4_31b_cerebras` 키는 위치만 이동(값 동일).
- `llm_profiles.py`: **Qwen 3.5 Flash 폴백 프로필(`OPENROUTER_FALLBACK_SELECTION_ALIAS_QWEN35_FLASH`) 삭제** — 더 이상 폴백 옵션으로 선택 불가.
- Gemini 3 Flash / 3.1 Flash-Lite BYOK는 메인 선택 alias에 **유지**되지만 managed 소스 로직이 반전(BYOK일 때만 alias 반환).
- **관련 파일**: `config/llm_profiles.py`, `data/i18n/*.json`

---

## C. 설정 화면 UI 변경

### 설정 모달 섹션 헤더
- `settings_modal.py`: 섹션 헤더 **18pt → 14pt**, 패딩/구조 변경(비첫 섹션 전 8pt 간격 컨테이너 제거 → `left=8, top=8` 인라인 컨테이너). 모든 선택 모달(모델·폴백·텔레메트리 등)에 적용.

### API 키 필드 순서 변경
- dev: deepgram, soniox, google, deepseek, cerebras, alibaba(북경/싱가포르), openrouter, openrouter_pkce
- vnext: deepgram, soniox, google, **openrouter, openrouter_pkce**, deepseek, cerebras, alibaba(북경/싱가포르)
- (OpenRouter 그룹이 맨 앞으로 이동)
- **참고**: vnext에서 `_cerebras_key`의 SecretStore 로딩/복원/`apply_locale` 호출이 settings 뷰에서 제거됨(가시성 로직은 유지되나 별도 경로로 로드되는 것으로 보임).

### 텔레메트리 설정 카드
- i18n 키 전면 개편:
  - `settings.telemetry.title`: `활성 데이터 전송` → `익명 텔레메트리`
  - `state_on`/`state_off` → `state.on`/`state.off` (점 표기법으로 변경, 이전 키는 삭제)
  - `settings.telemetry.on.description` 삭제 → `settings.telemetry.option.allow.description`/`option.decline.description` 신규 (거부 시 "익명 식별자와 전송일 기록을 지웁니다" 설명 추가)
  - 모달 제목 키 신규: `settings.telemetry.modal.title`
- **관련 파일**: `ui/views/settings.py`, `ui/components/settings/settings_modal.py`

### 디버그 미리보기 패널
- `debug_preview.telemetry_consent_modal` → `debug_preview.telemetry_consent`(이름 변경)
- 신규 액션 2개: `debug_preview.qq_auth_recoverable_error`("QQ 복구 가능 오류"), `debug_preview.qq_auth_translation_gated`("QQ 번역 게이트")
- **관련 파일**: `ui/components/debug_preview_panel.py`

---

## D. 동작(플로우) 변경

> **조사 보강 (2026-07-04)**: 코드 diff 전수 검증 결과, 기존 6개 항목 중 1개 정정·5개 확인 및 보강, 추가로 **11개 신규 동작 변경** 발견.

### 1. 대시보드 입력 / VRChat 타이핑
- **제출 시 타이핑**: dev는 제출한 발화의 번역/출력 작업이 끝날 때까지 타이핑 표시 유지(`_wait_for_manual_submit_output()`로 `translation_tasks[utterance_id]` 대기) → vnext는 `hub.submit_text()`가 **반환하는 즉시** 타이핑 해제. 번역이 비동기로 계속 진행 중이어도 VRChat 타이핑 표시가 먼저 사라질 수 있음.
- **입력 유휴 타이핑**: 3초 타임아웃은 동일하나 **구현 변경** — dev는 250ms 폴링 루프 → vnext는 단일 3초 `asyncio` 타임아웃 태스크(이벤트마다 재스케줄). 루프가 없으면 `page.run_task` 폴백도 제거되고 경고 로그만 남김.
- **활동 방출 주체 이관**: dev는 `DashboardView`가 포커스 해제 시 `_on_message_input_activity(False)` 호출 → vnext는 `DisplayCard`가 제출·포커스 해제 시 `_emit_input_activity(False)` 직접 방출. `dashboard.py`의 포커스 해제 활동 해제 코드 제거.
- **셀프 스피치 타이핑 사유 제거 (NEW)**: dev는 셀프 `SpeechEnd` 시 명명된 OSC 타이핑 사유 `"self_speech_pending"` 설정, `_enqueue_osc()`에서 해제 → vnext는 명명 사유 제거, `osc.send_typing(True)` 직접 호출 후 채팅박스 출력 게시 시 해제. `ChatboxPaginator.send_typing(False)`는 활성 명명 사유가 있으면 유지.
- **컨트롤러 정지 시 타이핑 정리 (NEW)**: dev는 `_reset_manual_typing_state()`로 추적 중인 수동 사유만 해제 → vnext는 `release_manual_typing()`이 `osc.clear_typing_reasons()` 호출해 **모든 명명 타이핑 사유** 해제.
- **수동 제출 사유명 (NEW)**: dev `"manual_submit_pending"` → vnext `manual_submit:{generation}`(세대별 고유).
- **관련 파일**: `ui/components/display_card.py`, `ui/views/dashboard.py`, `ui/controller.py`, `core/orchestrator/hub.py`, `core/runtime/output.py`, `core/osc/chatbox_paginator.py`

### 2. QQ 인증 플로우
- **빈 필드 처리 (정정)**: 빈 필드는 dev·vnext **모두** 다이얼로그에서 즉시 차단(`qq_auth.error.invalid_input` 인라인 표시). 기존 문서의 "vnext는 인증 서비스로 전송 후 인라인 에러" 설명은 **오류**. **실제 변경점은 입력 검증 완화** — dev는 QQ번호 길이·유니코드 카테고리·64자 헥스 정규식(`\A[0-9a-f]{64}\Z`) 검증 → vnext는 빈 값만 체크. 비어있지 않지만 잘못된 형식의 값이 이제 인증 서비스까지 전달됨.
- **에러 키 확장**: dev는 알 수 없는 실패를 `qq_auth.error.retry`로 통일 → vnext는 서비스 레이어 `qq_managed_auth.*` 메시지 참조를 `qq_auth.error.*`로 매핑해 구분 표시. 매핑표:
  | 서비스 키 (`qq_managed_auth.*`) | 다이얼로그 키 (`qq_auth.error.*`) |
  |---|---|
  | `already_claimed_discord` | `already_claimed_discord` |
  | `invalid_credential` / `mismatch` | `credential_mismatch` |
  | `lifetime_used` | `lifetime_used` |
  | `rate_limited` | `rate_limited` |
  | `key_unavailable` | `key_unavailable` |
  | `broker_unavailable` | `broker_unavailable` |
  | `settings_commit_failed` | `settings_commit_failed` |
  | `secret_write_failed` | `secret_write_failed` |
  | `error.retry` | `retry` |
- **Managed China 키 누락**: dev는 번역 토글 비활성화 + 스낵바 `qq_auth.error.key_unavailable` → vnext는 릴리스 준비 후 `result.message_key == "qq_managed_auth.required"` 시 QQ 인증 다이얼로그 열기.
- **번역 토글 인증 분기**: dev는 primary가 `MANAGED_CHINA`일 때만 QQ → vnext는 primary + 폴백 **모든 브랜치** 중 하나라도 `MANAGED_CHINA`면 QQ, 아니면 Discord. `_managed_openrouter_branch_settings_for()`가 primary·폴백 managed 연결을 모두 수집.
- **Managed 폴백 범위**: vnext는 managed 폴백 연결도 인증·사용량 새로고침·창작자 편지 소진 흐름에 포함.
- **트랜잭션 서비스 도입 (NEW)**: `app/services/qq_managed_auth.py` 추가 — 클레임 사전검사 → broker QQ 어설션 → 기존 비밀 스냅샷 → QQ 비밀 작성 → 자격 부여 → 클레임 기록 → 관리형 식별자 영속화의 트랜잭션 흐름. dev의 컨트롤러 직접 `prepare_from_qq_assertion()` 대체.
- **클레임 충돌 서비스화 (NEW)**: `ManagedAuthClaimGuard`(`app/services/managed_auth_claims.py`)가 Discord/QQ 충돌 판단. 비밀에서 로컬 클레임 소스를 역추적(backfill)하고, 충돌 시 `TransactionResult(status="provider_verification_failed")` 반환.
- **인증 성공 후 번역 활성화 (NEW)**: dev는 다이얼로그 완료 API(`complete_success`)로 처리 → vnext는 앱 레이어에서 `controller.set_translation_enabled(True)` 직접 호출 후 다이얼로그 닫기 + 성공 스낵바. 번역 활성화 실패 시 인라인 `qq_auth.error.retry`.
- **다이얼로그 API 단순화 (NEW)**: `on_submit`/`on_completion_message`/`auth_generation`/`set_recoverable_error`/`complete_*` → `on_continue`/`set_waiting`/`set_error`/`close` 4개로 축소. 상태·세대 관리는 앱 레이어(`OAuthRuntime`)로 이관.
- **관련 파일**: `ui/controller.py`, `ui/components/qq_managed_auth_dialog.py`, `ui/app.py`, `app/services/qq_managed_auth.py`, `app/services/managed_auth_claims.py`

### 3. Discord 인증
- **본문 문구**: `700회 이상 번역할 수 있어요` → `발화 기준 약 600~700회를 번역할 수 있어요` (4개 로케일 모두).
- **클레임 충돌**: dev는 하드코딩 `discord_auth.error.already_claimed_qq` → vnext는 `ManagedAuthClaimGuard.preflight()` 결과 메시지 사용. **단, QQ가 충돌 원인일 때는 기존 `discord_auth.error.already_claimed_qq` 키가 여전히 사용됨.** 알 수 없는/모호한 충돌은 신규 `managed_auth.error.claim_conflict`("이 기기에는 이미 다른 Managed 인증 기록이 있어요").
- **트랜잭션 서비스화 (NEW)**: `ManagedIdentityPreflightAdapter`·`DiscordOAuthAuthAdapter`·`DiscordManagedBrokerClientAdapter`·`ManagedConnectionAuthService`·`ManagedAuthClaimGuard` 조합의 트랜잭션 경로 추가. 릴리스 서비스가 트랜잭션 계약을 지원하지 않으면 `_start_discord_managed_auth_via_release_service()` 폴백(이 경로도 `ManagedAuthClaimGuard.preflight()` 선행).
- **LLM 프로바이더 재빌드 (NEW)**: dev는 `hub.llm is None`일 때만 재빌드 → vnext는 인증 성공 후 **항상** `await self._rebuild_llm_provider()` 호출.
- **파라미터 보존 (NEW)**: 실패 시 `result.message.params`를 스낵바에 전달 → `{retry_after_ms}` 등 파라미터화된 메시지 표시 가능.
- **관련 파일**: `ui/controller.py`, `ui/app.py`, `app/services/managed_auth_claims.py`

### 4. STT 토글
- **끄기/재시작**: dev는 즉시 `hub.stt.close()` → vnext는 `hub.drain_self_stt_for_toggle_off()`(가능하면) 호출, 불가능하면 `hub.stt.close()` 폴백.
- **드레인 동작 (NEW)**: 활성 세션/컨슈머 태스크가 있으면 `_drain_and_close(allow_finalize=True)` — 진행 중 발화를 **파이널라이즈** 후 `session.stop()`, 컨슈머 태스크를 `drain_timeout_s`(`settings.stt.drain_timeout_s`)까지 대기, 초과 시 취소. 파이널 결과가 도착하면 정상 STT 파이널 경로를 따라 번역/출력까지 진행될 수 있음.
- **토글 ON**: 일반 켜기 흐름은 크게 변경 없음. 단, 재시작(켜기 상태에서 재시작)은 드레인 후 재시작.
- **관련 파일**: `ui/controller.py`, `core/orchestrator/hub.py`, `core/runtime/provider_handle.py`, `core/stt/controller.py`

### 5. Peer 번역 EULA 수락
- **적용 경로**: dev는 설정 직접 변이 + `save_settings()` → vnext는 `copy.deepcopy(settings)` 후 `controller.apply_settings(updated)` 경유.
- **apply_settings 확장 (NEW)**: `apply_settings()`는 단순 할당+저장이 아니라 4개 뮤테이션 서비스(STT/언어/오디오, 오버레이/OSC 출력, UI/프롬프트/클립보드, 폴백 직접 적용)를 순회하며 런타임 적용 시도. 실패 시 `settings.mutation.runtime_apply_failed`. 직접 적용 경로도 오버레이 타겟 변경 시 활성 오버레이 중지, 언어 변경 시 peer 런타임 상태 초기화, STT 재시작 등 추가 처리.
- **동일 패턴 전파 (NEW)**: 텔레메트리 동의(`with_telemetry_consent`), 최근 언어 기록도 동일하게 `apply_settings()` 경유로 변경.
- **관련 파일**: `ui/app.py`, `ui/controller.py`, `app/services/settings_mutation.py`, `app/services/provider_runtime_apply.py`

### 6. 신규 에러 메시지
- **문서화된 3키**:
  - `provider.failure`: "번역 제공자 오류가 발생했어요({category}). 다시 시도해 주세요."
  - `stt.failure`: "음성 인식 오류가 발생했어요({category}). 다시 시도해 주세요."
  - `settings.mutation.runtime_apply_failed`: "설정은 저장됐지만 런타임 적용에 실패했어요."
- **전체 신규 에러 키 목록 (NEW)**: `managed_auth.error.claim_conflict`, `qq_auth.error.rate_limited`·`broker_unavailable`·`settings_commit_failed`·`secret_write_failed`, `qq_managed_auth.*` 12개 패밀리, `debug_preview.qq_auth_recoverable_error`·`qq_auth_translation_gated`.
- **`{category}` 플레이스홀더 (NEW)**: `auth`/`network`/`timeout`/`quota`/`rate_limit`/`invalid_response`/`service_unavailable`/`lifecycle`/`transaction`/`unknown` 10개 카테고리. **로케일별 번역 없음** — 사용자에게 raw 영문 식별자가 그대로 표시됨.
- **메시지 참조 패턴 (NEW)**: core/앱 서비스는 `UserMessageRef(key, params, severity)` 내보내고, UI 경계에서 `localize_user_message_ref()`가 i18n 번역. core 코드에 현지화 문자열/예외 텍스트 없음.
- **방출 지점**: `provider.failure` — `core/error_messages.py:provider_failure_report()` → hub.py 및 LLM 프로바이더(cerebras/deepseek/openrouter/qwen). `stt.failure` — `stt_failure_report()` → hub.py, stt/controller.py, peer_channel.py.
- **관련 파일**: `core/error_messages.py`, `core/messages.py`, `ui/i18n.py`, `ui/event_dispatch.py`

### 7. Peer 채팅박스 출력 차단 (NEW)
- **변경**: dev는 `_should_publish_to_chatbox()`가 `runtime.channel == self.active_chatbox_channel`로 라우팅 → peer 활성 채널이면 peer 발화가 채팅박스에 게시됨. vnext는 `_should_publish_to_chatbox()`가 `runtime.channel == "self"`로 **고정**. peer 발화는 `_should_deny_peer_chatbox_attempt()`(`channel == "peer" and active_chatbox_channel == "peer"`)로 감지해 `_deny_peer_chatbox_attempt()` 호출 — 빈 텍스트로 `output_runtime.publish_chatbox(channel="peer")` 게시.
- **사용자 영향**: peer 번역 활성화 시 VRChat 채팅박스에 peer 발화가 더 이상 표시되지 않음. 오버레이 자막으로만 라우팅.
- **관련 파일**: `core/orchestrator/hub.py`, `core/runtime/output.py`

### 8. 시스템 공개 채팅박스 라우팅 (NEW)
- **변경**: dev는 peer 번역 공개 공지를 일반 OSC 큐에 직접 enqueue → vnext는 `output_runtime.publish_system_disclosure_chatbox(text=text)`로 별도 시스템 공개 채널 사용. 텍스트는 리덕션 처리 후 채팅박스 싱크로 전달.
- **관련 파일**: `core/orchestrator/hub.py`, `core/runtime/output.py`

### 9. 오류 표시 위생화 / 정돈 (NEW)
- **변경**: dev는 오류 발생 시 `str(payload)`를 대시보드에 직접 표시 → vnext는 `ui/event_dispatch.py`에서 `UserMessageRef`→i18n 번역, `UserErrorReport`→메시지 참조(진단은 싱크별 리덕션), raw 문자열→`sanitize_legacy_raw_user_visible_error_text` 통과. raw payload는 디프리케이션 진단 출력.
- **사용자 영향**: 대시보드/스낵바에 raw 예외 문자열·스택 트레이스·비밀 노출 감소. 더 일반적이지만 안전한 메시지로 대체.
- **관련 파일**: `ui/event_dispatch.py`, `ui/event_mapping.py`, `ui/event_projection.py`

### 10. OAuth 작업 생명주기 중앙화 (NEW)
- **변경**: vnext는 `OAuthRuntime` 추가 — Discord·QQ 인증 다이얼로그 태스크를 `start_external_task()`로 시작. 종료/취소 시 등록된 외부 태스크 취소. 세대 검증으로 stale 인증 완료 가드.
- **사용자 영향**: 인증 다이얼로그 닫기/취소가 더 안정적.
- **관련 파일**: `ui/app.py`

### 11. 오버레이 런타임 소유권 / 교정 지속화 (NEW)
- **소유권**: dev는 개별 필드(`_overlay_bridge`/`_overlay_presenter`/`_overlay_manager`/`_overlay_diagnostics`/`_overlay_start_task`) → vnext는 `OverlayRuntimeHandle` + `_overlay_lock` + `_active_overlay_target`로 통합. 오버레이 타겟 변경 시 활성 오버레이를 먼저 중지 후 전환.
- **교정 지속화**: dev는 교정 적용 시 즉시 설정 저장 → vnext는 `_schedule_overlay_calibration_persistence(...)`로 지연/스케줄 저장(비동기, 블로킹 감소).
- **관련 파일**: `ui/controller.py`, `ui/desktop_overlay.py`

### 12. 언어 변경 시 peer 런타임 상태 초기화 (NEW)
- **변경**: vnext `apply_settings()`는 peer 유효 소스/타겟 언어 변경 감지 시 `hub.clear_language_runtime_state(channel="peer")` 호출.
- **사용자 영향**: peer 번역 컨텍스트/상태가 언어 변경 시 초기화되어 이전 언어 잔류 감소.
- **관련 파일**: `ui/controller.py`

### 13. 수동 입력 활동 비동기 스케줄링 (NEW)
- **변경**: dev는 컨트롤러 직접 호출 `note_manual_input_activity(...)` → vnext는 `page.run_task(_task)` + `controller.set_manual_input_activity(has_text)`로 비동기 스케줄.
- **사용자 영향**: 타이핑/유휴 반영이 동기가 아닌 비동기로 스케줄됨(미세 타이밍 차이).
- **관련 파일**: `ui/app.py`, `ui/controller.py`

### 14. GitHub Star 프롬프트 런타임 (NEW)
- **변경**: vnext는 `GithubStarPromptRuntime` 추가 — close/취소/진단 포함. 번역 성공 관찰이 런타임/서비스 경로로 이관.
- **사용자 영향**: GitHub Star 프롬프트 타이밍/취소/충돌 동작이 dev와 다를 수 있음.
- **관련 파일**: `ui/app.py`, `ui/controller.py`

### 15. 시작 시 안정 설정 가져오기 (NEW)
- **변경**: `TranslatorApp`/`main_gui`가 `allow_stable_settings_import` 수락. vnext 설정 로드 경로에서 dev(stable) 설정 마이그레이션/가져오기 동작 추가.
- **사용자 영향**: 최초 vnext 실행 시 기존 dev 설정을 가져올 수 있음.
- **관련 파일**: `ui/app.py`, `ui/controller.py`, `config/settings_vnext/`

### 16. VAD 행오버 폴백 리터럴 1.1 (NEW)
- **변경**: 여러 경로에서 `DEFAULT_STABLE_VAD_HANGOVER_MS / 1000.0` → 리터럴 `1.1`(초)로 변경.
- **사용자 영향**: 상수가 향후 변경돼도 해당 경로는 1.1초로 고정됨.
- **관련 파일**: `ui/controller.py`

### 17. 프로바이더 런타임 교체 생명주기화 (NEW)
- **변경**: `ClientHub`가 `ProviderRuntimeHandle`로 self STT·peer STT·LLM 관리. 교체 시 직접 태스크 취소/프로바이더 close 대신 핸들 경유로 drain/stop.
- **사용자 영향**: stale 프로바이더 완료 감소, 종료 시 close 실패가 표면화, STT/LLM 교체 동작이 더 엄격.
- **관련 파일**: `core/orchestrator/hub.py`, `core/runtime/provider_handle.py`

---

## E. zh-CN 전용 차이 (다른 로케일에 없음)

| 키 | dev | vnext |
|---|---|---|
| `dashboard.peer_label` | 聆听 | **PEER** (영문 회귀) |
| `dashboard.overlay_label` | 字幕 | **Subtitles** (영문 회귀) |
| `provider.soniox.description` | 语音识别性能最好**(中文除外)** | 语音识别性能最好 (괄호 삭제) |
| `provider.local_qwen.description` | 不占用**显存** | 不占用 **VRAM** (용어 변경) |

> ko/en/ja에는 peer_label/overlay_label이 애초에 영문(PEER/Subtitles)이므로, dev의 zh-CN만 번역돼 있던 것을 vnext에서 영문으로 되돌린 것.

---

## F. 아키텍처 (사용자 간접 영향)

- `config/settings_vnext/` 신규 모듈(schema/facade/migration/compat/serialization) — vnext 설정 시스템 전면 교체. `8fcae1f fix(config): detect vnext settings by shape` 커밋으로 dev 설정을 shape로 감지.
- `with_telemetry_consent()` 헬퍼 추가.
- `ui/i18n.py`: `localize_user_message_ref()` 추가(`UserMessageRef` → 로컬라이즈). core가 message reference를 내보내고 UI에서 번역하는 패턴 도입.
- `config/overlay_calibration.py`로 `OverlayCalibration` 데이터클래스 이동(`ui/overlay_calibration.py`는 re-export-only).

---

## G. 요약 — 사용자가 체감하는 주요 차이

| 분류 | 항목 수 | 비고 |
|---|---|---|
| 모달 재작성 | 3 | 텔레메트리·QQ인증·폴백 |
| 모델/프로바이더 | 6+ | Cerebras 설명, 모델 설명 이동, Gemini BYOK 라벨 삭제, Qwen 폴백 제거 |
| 설정 화면 UI | 3 | 섹션 헤더 축소, API 키 순서, 텔레메트리 카드 |
| 동작 플로우 | 17 | VRChat 타이핑(6), QQ 인증(9), Discord(5), STT 드레인(3), EULA(3), 에러 메시지(6), **신규 11**: peer 채팅박스 차단·시스템 공개 라우팅·오류 위생화·OAuth 생명주기·오버레이 소유권·언어별 peer 초기화·입력 비동기·Star 프롬프트·설정 가져오기·VAD 리터럴·프로바이더 생명주기 |
| zh-CN 전용 | 4 | peer/overlay 라벨 회귀, 용어 변경 |
| 신규 에러 메시지 | 3개 키군 | provider/stt failure, claim_conflict, QQ 에러 확장 |

알려주신 5개 예시(텔레메트리 동의 모달, QQ 인증 인증서 폼, 폴백 선택 모달, Cerebras Gemma 4 설명, 언어 선택 카드 헤더) 외에 **약 35개 이상의 추가 사용자 노출 차이**를 확인함. 특히 zh-CN 라벨 회귀(peer_label/overlay_label)와 Qwen 3.5 Flash 폴백 제거는 사용자가 직접 마주칠 수 있는 눈에 띄는 변화.

### D절 보강 결과 (2026-07-04 2차)
- **정정 1건**: QQ 빈 필드 처리 — 기존 "서비스 전송 후 인라인 에러"는 오류. 빈 필드는 양쪽 모두 다이얼로그에서 차단. 실제 변경은 64자 헥스 정규식 등 **엄격한 형식 검증 제거**.
- **보강 5건**: VRChat 타이핑(셀프 스피치 사유 제거·정리 확대·사유명 변경 등 4개 추가), QQ 인증(트랜잭션 서비스·클레임 가드·번역 활성화·API 단순화 등 4개 추가), Discord(트랜잭션화·LLM 재빌드·파라미터 보존 등 3개 추가), STT 드레인(파이널라이즈·타임아웃 상세), EULA(apply_settings 4개 뮤테이션 서비스·동일 패턴 전파), 에러 메시지(전체 키 목록·`{category}` 10종 raw 영문·UserMessageRef 패턴·방출 지점).
- **신규 11건**: peer 채팅박스 차단(#7), 시스템 공개 라우팅(#8), 오류 위생화(#9), OAuth 중앙화(#10), 오버레이 소유권/교정 지연(#11), 언어별 peer 초기화(#12), 입력 비동기(#13), Star 프롬프트(#14), 설정 가져오기(#15), VAD 리터럴(#16), 프로바이더 생명주기(#17).
