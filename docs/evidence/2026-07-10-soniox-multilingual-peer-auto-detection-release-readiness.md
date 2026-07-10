# Soniox Multilingual Peer Auto-Detection Release-Readiness Evidence

Date: 2026-07-10

## Scope and limitation

This is redacted, deterministic simulated evidence for the approved release-readiness gate. It is not real-device, real-loopback, or production validation, and it does not claim production readiness or deployment.

The approved deterministic replacement ran controlled coverage twice for Korean-only, Japanese-only, generic Chinese/Taiwan-Mandarin, ja→zh, zh→ja→ko, and same-language-pause scenarios. It also ran deterministic normal-turn and limited-overlap simulations for four zh/ja/ko participants.

## Retained diagnostic-safe records

| Simulation | Simulated | Language sequence | Segment count | Latency | Failures |
| --- | --- | --- | ---: | ---: | --- |
| Normal turns | yes | zh, ja, ko, zh | 4 | 760 ms (simulated schedule span) | none observed |
| Limited overlap | yes | zh, ja, ko, zh | 4 | 850 ms (simulated schedule span) | none observed |

No speech text, audio, raw provider payload, credential, secret, or real transcript is retained in this record. Controlled fixture token data exists only in the automated test source and is not reproduced here.

## Verification results

Python commands ran from the assigned worktree with `C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe`.

- `& "C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe" -m pytest tests/core/test_soniox_multilingual_release_readiness.py`
  - Result: passed, 8 tests. This deterministic control run executes every prescribed controlled scenario twice and the normal/limited-overlap four-participant simulation without a provider connection, audio input, VRChat, or secrets.
- `& "C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe" -m pytest -p no:cacheprovider tests/core/test_soniox_multilingual_release_readiness.py`
  - Result: passed, 8 tests. This is the required second simulation/control run.
- `& "C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe" -m pytest tests/providers/test_soniox_backend.py tests/integration/test_soniox_stt_integration.py`
  - Result: passed, 25 tests; 1 integration test skipped because `INTEGRATION=1` was not set. No external Soniox connection was made.
- `& "C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe" -m pytest tests/core/test_hub_overlay_streaming.py tests/core/test_observability_output_contracts.py tests/core/runtime/test_output_runtime.py`
  - Result: passed, 89 tests.
- `& "C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe" -m ruff check src tests`
  - Result: passed, all checks passed.
- `git diff --check`
  - Result: passed.

The deterministic model provides four simulated participants with normal sequential and limited-overlap schedules; the limited-overlap schedule contains a verified overlapping interval. Its language-run order, segment count, terminal output order, parent closure, and peer-chatbox denial are observed through the owner/output path. Latency is the derived simulated schedule span, not device or provider latency; failures are derived from the observed structural checks.

The same-language-pause control sends distinct controlled token batches, observes retained parsed timing, and retains one adjacent-language segment. Each simulated parent has one chronological terminal trace in which every child terminal outcome precedes `parent_closed`. Peer-original presentation is exercised with the preference enabled and disabled: enabled preserves the original presentation, while disabled marks that presentation suppressed; translation, closure, and the peer-chatbox hard deny remain intact in both states. The peer presentation/event contract retains the primary translation and optional original only; no speaker or language labels are introduced.

The deterministic controls verify token order and no loss/duplication, generic `zh` normalization, ordered child terminal output, parent closure, optional-original preservation, and the peer-chatbox hard deny. The gate remains blocked if a required command fails, a scenario is missing, a parent remains unclosed, peer chatbox publication is observed, or an unsafe artifact is introduced.

## Gate decision

The deterministic simulation replacement is limited evidence for the approved scope only. It does not waive privacy assertions, does not establish real-device or real-loopback behavior, and does not establish production readiness.
