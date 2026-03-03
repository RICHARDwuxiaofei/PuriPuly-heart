# Coverage 80% Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task with spec review then code-quality review after each task.

**Goal:** Raise test coverage for `src/puripuly_heart` from 62.13% to at least 80% and keep `push-ci` coverage gate stable.

**Architecture:** Drive coverage by targeting highest-miss modules first (UI controller/views + orchestrator + STT providers), using deterministic test doubles and monkeypatch seams to avoid network/audio/device dependencies. Expand existing test suites instead of introducing new frameworks.

**Tech Stack:** `pytest`, `pytest-asyncio`, `pytest-cov`, `monkeypatch`, `flet` (with `pytest.importorskip("flet")`), GitHub Actions.

---

## Baseline (2026-03-03)

- Coverage command:
  - `python -m pytest --ignore=tests/integration --cov=src/puripuly_heart --cov-report=term-missing --cov-fail-under=80`
- Current result:
  - `TOTAL 62.13%` (`3951/6359` covered)
  - Additional covered lines needed for 80%: **1137 lines**
- Highest impact files (missing lines):
  - `src/puripuly_heart/ui/controller.py` (377)
  - `src/puripuly_heart/core/orchestrator/hub.py` (185)
  - `src/puripuly_heart/ui/views/settings.py` (184)
  - `src/puripuly_heart/providers/stt/deepgram.py` (133)
  - `src/puripuly_heart/providers/stt/qwen_asr.py` (128)

## Milestone Targets

- Milestone 1 (Tasks 1-2): >= 68%
- Milestone 2 (Tasks 3-4): >= 74%
- Milestone 3 (Tasks 5-6): >= 78%
- Milestone 4 (Task 7): >= 80%
- Milestone 5 (Tasks 8-9): local gate pass + two consecutive CI runs green at >= 80%

## Task Coverage Delta Targets (Quantitative)

- Task 1 target: +220 covered lines (~+3.46%)
- Task 2 target: +280 covered lines (~+4.40%)
- Task 3 target: +220 covered lines (~+3.46%)
- Task 4 target: +180 covered lines (~+2.83%)
- Task 5 target: +130 covered lines (~+2.04%)
- Task 6 target: +90 covered lines (~+1.42%)
- Task 7 target: +60 covered lines (~+0.94%)
- Planned total: +1180 covered lines (buffer over required +1137)

## Re-planning Rule

- After each task, run full coverage command and record delta.
- If cumulative gain is below target by >10% at any milestone boundary, stop and reprioritize to highest-miss files first:
  - `src/puripuly_heart/ui/controller.py`
  - `src/puripuly_heart/ui/views/settings.py`
  - `src/puripuly_heart/core/orchestrator/hub.py`
  - `src/puripuly_heart/providers/stt/deepgram.py`
  - `src/puripuly_heart/providers/stt/qwen_asr.py`

### Task 1: Expand ClientHub Branch Coverage

**Files:**
- Create: `tests/core/test_hub_branch_coverage.py`
- Modify: `tests/helpers/fakes.py`
- Test: `tests/core/test_hub_branch_coverage.py`

**Coverage Delta Target:** +220 lines

**Step 1: Write the failing test**

```python
async def test_hub_drops_stale_partial_and_keeps_final_order(...):
    ...
```

Cover branches currently untested in `hub.py`: partial/final ordering, retry/fallback paths, queue clear/reset paths.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/core/test_hub_branch_coverage.py -q`
Expected: FAIL with missing behavior/branch assertions.

**Step 3: Write minimal implementation**

If tests expose missing seams, make minimal changes in `src/puripuly_heart/core/orchestrator/hub.py` for deterministic injection points only (no feature changes).

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/core/test_hub_branch_coverage.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/core/test_hub_branch_coverage.py tests/helpers/fakes.py src/puripuly_heart/core/orchestrator/hub.py
git commit -m "test: raise hub branch coverage with deterministic edge-case tests"
```

### Task 2: Expand GuiController Decision Coverage

**Files:**
- Create: `tests/ui/test_controller_branch_paths.py`
- Modify: `tests/ui/test_controller_api_verification.py`
- Test: `tests/ui/test_controller_branch_paths.py`

**Coverage Delta Target:** +280 lines

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_verify_and_update_status_handles_mixed_provider_failures(...):
    ...
```

Target untested branches in `ui/controller.py`: provider/key combinations, fallback model paths, disabled toggles, status mapping.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ui/test_controller_branch_paths.py -q`
Expected: FAIL.

**Step 3: Write minimal implementation**

Only add minimal guards/seams in `src/puripuly_heart/ui/controller.py` if required for deterministic tests.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ui/test_controller_branch_paths.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/ui/test_controller_branch_paths.py tests/ui/test_controller_api_verification.py src/puripuly_heart/ui/controller.py
git commit -m "test: cover gui controller provider and verification branches"
```

### Task 3: Cover Settings View Callback/State Branches

**Files:**
- Create: `tests/ui/test_settings_view_branches.py`
- Modify: `tests/ui/test_settings_prompt_switching.py`
- Modify: `tests/ui/test_app_secret_clear.py`
- Test: `tests/ui/test_settings_view_branches.py`

**Coverage Delta Target:** +220 lines

**Step 1: Write the failing test**

```python
def test_settings_view_persists_provider_switch_and_prompt_state(...):
    ...
```

Cover `ui/views/settings.py` branches: modal selections, prompt persistence, verification status transitions, save/apply/cancel paths.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ui/test_settings_view_branches.py -q`
Expected: FAIL.

**Step 3: Write minimal implementation**

If needed, add test seams only in `src/puripuly_heart/ui/views/settings.py`.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ui/test_settings_view_branches.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/ui/test_settings_view_branches.py tests/ui/test_settings_prompt_switching.py tests/ui/test_app_secret_clear.py src/puripuly_heart/ui/views/settings.py
git commit -m "test: increase settings view coverage for callback/state branches"
```

### Task 4: Cover STT Provider Error and Lifecycle Branches

**Files:**
- Modify: `tests/providers/test_deepgram_session.py`
- Modify: `tests/providers/test_deepgram_backend.py`
- Modify: `tests/providers/test_qwen_asr_session.py`
- Modify: `tests/providers/test_qwen_asr_backend.py`
- Test: `tests/providers/test_deepgram_session.py`
- Test: `tests/providers/test_qwen_asr_session.py`

**Coverage Delta Target:** +180 lines

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_deepgram_session_reports_transport_error_once(...):
    ...
```

Add tests for reconnect timeout, duplicate error suppression, close/stop idempotency, event termination ordering.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/providers/test_deepgram_session.py tests/providers/test_qwen_asr_session.py -q`
Expected: FAIL.

**Step 3: Write minimal implementation**

Make minimal lifecycle-safety fixes in `src/puripuly_heart/providers/stt/deepgram.py` and `src/puripuly_heart/providers/stt/qwen_asr.py` if tests reveal real defects.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/providers/test_deepgram_session.py tests/providers/test_qwen_asr_session.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/providers/test_deepgram_session.py tests/providers/test_deepgram_backend.py tests/providers/test_qwen_asr_session.py tests/providers/test_qwen_asr_backend.py src/puripuly_heart/providers/stt/deepgram.py src/puripuly_heart/providers/stt/qwen_asr.py
git commit -m "test: cover stt provider lifecycle and transport error branches"
```

### Task 5: Cover UI App/Event Bridge/Dashboard Glue Paths

**Files:**
- Create: `tests/ui/test_event_bridge.py`
- Create: `tests/ui/test_dashboard_view_branches.py`
- Modify: `tests/ui/test_logs_view.py`
- Modify: `tests/ui/test_app_secret_clear.py`
- Test: `tests/ui/test_event_bridge.py`
- Test: `tests/ui/test_dashboard_view_branches.py`

**Coverage Delta Target:** +130 lines

**Step 1: Write the failing test**

```python
def test_event_bridge_ignores_unknown_event_and_keeps_queue_alive(...):
    ...
```

Target low-coverage files: `ui/event_bridge.py`, `ui/views/dashboard.py`, `ui/app.py`.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ui/test_event_bridge.py tests/ui/test_dashboard_view_branches.py -q`
Expected: FAIL.

**Step 3: Write minimal implementation**

Add only stability fixes in UI glue code where tests expose real branch bugs.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ui/test_event_bridge.py tests/ui/test_dashboard_view_branches.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/ui/test_event_bridge.py tests/ui/test_dashboard_view_branches.py tests/ui/test_logs_view.py tests/ui/test_app_secret_clear.py src/puripuly_heart/ui/event_bridge.py src/puripuly_heart/ui/views/dashboard.py src/puripuly_heart/ui/app.py
git commit -m "test: increase ui glue coverage for event bridge and dashboard paths"
```

### Task 6: Cover Reusable UI Components With Deterministic Unit Tests

**Files:**
- Create: `tests/ui/test_display_card.py`
- Create: `tests/ui/test_language_modal.py`
- Create: `tests/ui/test_bottom_nav.py`
- Test: `tests/ui/test_display_card.py`
- Test: `tests/ui/test_language_modal.py`
- Test: `tests/ui/test_bottom_nav.py`

**Coverage Delta Target:** +90 lines

**Step 1: Write the failing test**

```python
def test_display_card_toggles_text_and_state_without_page_runtime(...):
    ...
```

Target branch-heavy UI component files under `src/puripuly_heart/ui/components/`.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ui/test_display_card.py tests/ui/test_language_modal.py tests/ui/test_bottom_nav.py -q`
Expected: FAIL.

**Step 3: Write minimal implementation**

Add minimal null-guard/test seam updates to component modules only if required.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ui/test_display_card.py tests/ui/test_language_modal.py tests/ui/test_bottom_nav.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/ui/test_display_card.py tests/ui/test_language_modal.py tests/ui/test_bottom_nav.py src/puripuly_heart/ui/components/display_card.py src/puripuly_heart/ui/components/language_modal.py src/puripuly_heart/ui/components/bottom_nav.py
git commit -m "test: add deterministic unit tests for core ui components"
```

### Task 7: Final Gap Closure to 80%

**Files:**
- Create: `tests/ui/test_about_view_branches.py`
- Modify: `tests/core/test_context_memory.py`
- Modify: `tests/core/test_hub_low_latency.py`
- Test: `tests/ui/test_about_view_branches.py`
- Test: `tests/core/test_hub_low_latency.py`

**Coverage Delta Target:** +60 lines

**Step 1: Write the failing test**

```python
def test_about_view_link_actions_handle_missing_page_gracefully(...):
    ...
```

Close remaining high-miss areas in `ui/views/about.py`, `core/orchestrator/hub.py` edge conditions.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ui/test_about_view_branches.py tests/core/test_hub_low_latency.py -q`
Expected: FAIL.

**Step 3: Write minimal implementation**

Apply only behavior-preserving fixes needed to make failing edge tests pass.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ui/test_about_view_branches.py tests/core/test_hub_low_latency.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/ui/test_about_view_branches.py tests/core/test_context_memory.py tests/core/test_hub_low_latency.py src/puripuly_heart/ui/views/about.py src/puripuly_heart/core/orchestrator/hub.py
git commit -m "test: close remaining branch gaps to reach 80 percent coverage"
```

### Task 8: Local Gate Validation (Operational, Non-TDD)

**Files:**
- Modify: `.github/workflows/push-ci.yml`
- Test: `tests` (non-integration)

**Step 1: Validate local gate command output**

No new unit test for this task. This is an operational gate validation task.

Run:
- `python -m pytest --ignore=tests/integration --cov=src/puripuly_heart --cov-report=term-missing --cov-fail-under=80`
Expected: PASS once Tasks 1-7 are complete.

**Step 2: Adjust only gate wiring if needed**

If needed, tune only CI/report formatting (not threshold reduction).

**Step 3: Run full local quality gate**

Run:
- `python -m ruff check src tests`
- `python -m black --check src tests`
- `python -m pytest --ignore=tests/integration --cov=src/puripuly_heart --cov-report=term-missing --cov-fail-under=80`
Expected: all PASS; coverage >= 80.

**Step 4: Commit**

```bash
git add .github/workflows/push-ci.yml
git commit -m "ci: lock 80 percent coverage gate"
```

### Task 9: Remote CI Stability Validation

**Files:**
- Test: GitHub Actions `Push CI` run history

**Step 1: Trigger first CI run**

Push a small no-op commit or rerun workflow with current HEAD.
Expected: `Push CI` green with coverage >= 80.

**Step 2: Trigger second CI run**

Push another small no-op commit or rerun workflow once more.
Expected: second consecutive green run with coverage >= 80.

**Step 3: Capture evidence**

Record run URLs, commit SHAs, and coverage output in `agents/logs/`.

**Step 4: Commit**

No code commit required for this validation-only task.

## Risk Management

- Avoid flaky tests by isolating network/audio/GUI runtime behind fakes.
- Keep production changes minimal and seam-focused.
- Preserve async safety in provider teardown (`await close()` paths).
- Keep integration tests excluded from push CI speed gate.

## Verification Contract

- For each task:
  - run targeted tests for the touched area
  - run full non-integration test suite with coverage
  - capture results in `agents/logs/` with command/output summary
- Final exit criteria:
  - `coverage >= 80%` locally and in CI
  - two consecutive green `push-ci` runs

## Out of Scope

- README/locale documentation sync is intentionally excluded from this 80% coverage recovery plan.
