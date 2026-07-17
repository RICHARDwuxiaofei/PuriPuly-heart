---
id: PRD-ISSUE-20-LOCAL-ASR-BACKENDS-001
status: reviewed
source: .agents/specs/prd/drafts/issue-20-local-asr-backends.source.r2.md
baseline_ref: vnext@2a2a35af442c2b6414e0d7ff22b42c9a4b57c427
integration_target: vnext
document_review_verdict: ready
blocking_open_decisions: 0
---

# Outcome

On supported Windows x64 systems, PuriPuly provides a complete required set of independently usable CPU local-ASR models with capability-based automatic routing, plus an explicitly installed strict-Vulkan GPU ASR option that remains safe and diagnosable while VRChat is competing for GPU memory. Model, worker, queue, settings, and failure behavior preserve application availability and the established separation of self, peer, and system channels.

# Established Baseline

## Code baseline

- The normative code baseline is `vnext@2a2a35af442c2b6414e0d7ff22b42c9a4b57c427`.
- `STTProviderName` and vnext settings intents currently expose `local_qwen` as the only local provider and persist separate self and peer STT selections.
- `local_qwen` uses a single Qwen3-ASR 0.6B int8 sherpa-onnx asset contract and `LocalDecodeCoordinator` for non-blocking FIFO decode.
- `LocalSTTDownloadRuntime`, the runtime installer, the packaged asset manifest, and `installer.iss` currently own local-model validation, repair, and installation behavior for that one model.
- No GPU STT provider or GPU worker process exists at the baseline.
- The orchestrator and provider runtime handles already keep self and peer provider lifecycles separate and await provider shutdown.

## User-visible surfaces

- Separate self and peer STT provider selectors in the Flet settings UI.
- Local-STT download, missing, invalid, progress, and failure status in the dashboard and settings experience.
- Source-language selection and its effect on STT provider capability.
- The Windows installer and its local-model provisioning result.
- Runtime warnings, toasts, progress, retry or repair actions, and detailed diagnostics.
- Existing self and peer VAD behavior: peer segments are capped at seven seconds; self has no established maximum segment duration.

## Actual product entrypoints

- Packaged Windows executable `PuriPulyHeart.exe`, entering through `puripuly_heart.main` and starting the Flet GUI.
- Source entrypoint `python -m puripuly_heart.main` with equivalent application behavior.
- Inno Setup installer defined by `installer.iss`.
- Existing independent native overlay `PuriPulyHeartOverlay.exe`.
- New internal native process `PuriPulyHeartGpuWorker.exe`, launched and supervised only by the desktop application when GPU STT is active.

## Platform and environment

- Production support is Windows 10 and Windows 11 on x64 hardware.
- Production GPU evidence requires a physical Vulkan-capable device and the packaged Windows composition; retained representative VRChat evidence verifies physical coexistence, while automated low-reported-VRAM controls verify advisory low-memory behavior.
- Automated Python evidence uses repository `.venv` on Windows.
- Native worker and overlay evidence uses the Windows Rust build environment.
- Linux/WSL with `.venv-wsl` may provide development and non-production helper evidence but cannot replace required Windows evidence.
- macOS is unsupported.
- Installer smoke evidence uses an alternate AppId and an isolated installation directory.

## Compatibility baseline

- Existing settings persist self and peer choices under established provider settings and accept `local_qwen` as the direct Qwen3-ASR 0.6B local provider identity.
- Existing settings files and keys remain compatibility inputs; settings must remain round-trippable and receive defaults for new fields.
- Persisted data is backed up before forward migration.
- Provider aliases, prompt fallbacks, SecretStore behavior, and explicit config behavior remain compatible.
- Peer utterances never route to the VRChat chatbox, and self, peer, and system outputs remain separate.
- The existing overlay executable, protocol, startup manifest, authenticated session, heartbeat, shutdown, and diagnostics contract remain compatible.
- Runtime diagnostics do not require transcript text or raw audio content.

# Scope

## Included

- Three independently installed and verified required CPU model assets.
- Four CPU choices: CPU Auto and one direct choice for each CPU model.
- Official-capability-based CPU Auto routing and completeness gating.
- Backed-up migration of existing `local_qwen` self and peer selections to CPU Auto while preserving `local_qwen` as the direct-Qwen identity.
- Persistent language-mismatch fallback to local Qwen3-ASR 0.6B.
- An explicitly installed Qwen3-ASR 1.7B GGUF Q6_K strict-Vulkan provider shared by self and peer.
- A separate supervised Rust GPU worker, global Vulkan-device selection, non-blocking preflight, model residency, cancellation, crash isolation, and manual recovery.
- Twelve-second pending-work expiry for local CPU and GPU decode, and shared-GPU global FIFO serialization.
- CPU and Vulkan per-attempt RTF diagnostics.
- Packaging, provenance, localization, native build, and Windows installer evidence required to ship the feature.

## Non-goals

### NG-001 — Additional production platforms

macOS support and Linux production distribution are not included.

### NG-002 — Automatic GPU installation

The GPU model is not bundled or downloaded without an explicit user opt-in action.

### NG-003 — Alternative GPU execution policy

CUDA-only execution, vendor whitelists, fixed current-free-VRAM minimums, and automatic GPU-to-CPU fallback are not included.

### NG-004 — Automatic GPU recovery

GPU OOM, Vulkan failure, device loss, or worker failure does not trigger automatic retry, provider replacement, or background recovery.

### NG-005 — Worker expansion

The GPU worker does not own microphone capture, VAD, provider selection, translation, VRChat routing, or presentation output.

### NG-006 — GPU-specific speech policy

Streaming, timestamps, worker-owned VAD, a provider-specific utterance cap, and changes to the established peer or self VAD duration policy are not included.

### NG-007 — Process consolidation

The GPU worker and existing overlay do not become one executable, service, or shared runtime process.

### NG-008 — End-to-end global ordering

Ordering across different STT providers, cloud STT, translation, and downstream outputs is not guaranteed by this contract.

### NG-009 — Driver-wide fault isolation

The application cannot guarantee survival from an operating-system or driver-wide GPU reset even though ordinary worker failures must be isolated.

# Requirements

## REQ-001 — CPU asset set and direct providers

The Windows installer must attempt to install Parakeet v3 multilingual int8, Parakeet Japanese int8, and Qwen3-ASR 0.6B int8. Each model must have an independent pinned asset manifest, expected SHA digest verification, installation state, repair target, and direct provider choice. A valid direct provider must remain usable independently of the other two models.

## REQ-002 — Partial CPU installation and CPU Auto gate

Application installation must complete when one or more CPU model downloads, installations, or validations fail. CPU Auto must be unavailable whenever any of the three required CPU models is missing, corrupt, stale, or otherwise invalid, and must become available without reinstalling the application once all three validate. Valid direct CPU providers remain selectable throughout partial failure.

## REQ-003 — Official-capability CPU Auto routing

CPU Auto must resolve the configured source-language code against the official capability sets pinned for the installed model revisions. Japanese resolves to Parakeet Japanese. Exact Parakeet v3 supported codes resolve to Parakeet v3. Other codes officially supported by Qwen3-ASR 0.6B resolve to Qwen3-ASR 0.6B. CPU Auto must not claim support for a code outside the union of those pinned official capability sets, and diagnostics must identify the resolved model.

## REQ-004 — Unavailable manually selected CPU model

When a directly selected CPU model is missing or invalid, the selection must remain persisted, the affected channel must not start or silently select another model, and the UI must identify the affected model and offer targeted repair. This failure must not disable a valid direct model selected by the other channel.

## REQ-005 — Backed-up settings migration

On the first supported forward load, every existing self or peer `local_qwen` selection must migrate to CPU Auto after an exact recoverable settings backup exists. Migration must preserve all unrelated settings, round-trip through canonical persistence, and be idempotent. The direct Qwen3-ASR 0.6B choice must continue to serialize as `local_qwen`; migration must not retire that established identity.

## REQ-006 — Persistent manual-language fallback

When a directly selected local model is incompatible with the configured source language and local Qwen3-ASR 0.6B supports that language, the affected channel must notify the user with the localized meaning `선택한 ASR 모델은 이 언어를 지원하지 않습니다. 로컬 Qwen3-ASR 0.6B를 사용합니다.`, switch to Qwen3-ASR 0.6B, persist `local_qwen`, and remain there until the user changes it. If that Qwen asset is missing or invalid, the persisted `local_qwen` selection remains, the channel stops, and targeted repair is offered without another fallback.

## REQ-007 — Explicit GPU provider and device selection

Qwen3-ASR 1.7B GGUF Q6_K must be exposed as a distinct self and peer STT provider using `transcribe.cpp` with strict Vulkan. Its model download and installation must require an explicit user opt-in and independent integrity validation. A single 1×1 GPU-device settings card must be visible whenever either channel selects GPU; it must offer Auto and enumerated Vulkan devices, and its persisted selection is shared by self and peer. An unavailable saved device must not cause automatic CPU fallback.

## REQ-008 — Non-blocking discovery and activation

After the first rendered UI frame, Vulkan-device discovery must run in the background without loading the model or blocking application interaction. Discovery still running after two seconds must be represented as pending, not failed. GPU activation must perform real model validation, load, and warmup with observable progress. Missing explicit installation, unsupported capability, and actual activation failure must be distinguishable states.

## REQ-009 — Isolated Rust GPU worker and ownership

All GPU device discovery, model load, warmup, inference, cancellation, and unload must occur in a separately supervised Rust process named `PuriPulyHeartGpuWorker.exe`; those operations must never execute in the Python application or overlay process. The worker must not be a Windows service or share the overlay executable. The application must own microphone capture, VAD, self and peer admission queues, provider choice, translation, and output routing. Worker communication must be authenticated and locally scoped, and lifecycle diagnostics must identify startup, readiness, heartbeat or liveness, failure, cancellation, and shutdown outcomes without logging raw audio or transcript content.

## REQ-010 — Shared model residency and terminal shutdown

Self and peer GPU channels must share one selected Vulkan device, one worker, and one loaded model. While at least one GPU channel is active, the worker and model remain resident without an idle timeout. A saved but inactive provider selection does not start the worker. When the last GPU channel is disabled or the application shuts down, the system must reject new GPU work, discard pending GPU work, cancel in-flight inference, and unload the model; if bounded cooperative cancellation does not complete, it must terminate the worker. A later activation starts a fresh load and warmup.

## REQ-011 — Advisory VRAM and manual GPU recovery

Reported current available VRAM must be advisory only. Low reported availability may warn but must not block activation, cancel inference, evict a resident model, or trigger fallback. The worker must attempt actual load and warmup on a usable Vulkan device. Unsupported Vulkan capability or an actual load, warmup, OOM, device, backend, or worker failure stops the affected GPU feature without automatic retry or CPU fallback, keeps the selected provider and installed asset, releases failed worker resources, and exposes a user-initiated retry. CPU ASR, the main application, and the existing overlay must remain usable after ordinary worker-process failure.

## REQ-012 — Local pending-work expiry

Every self or peer local CPU or GPU utterance waiting to start decode must expire 12 seconds after its `speech_end` time. There is no pending-count limit. Expiry must be checked before decode starts and must never cancel a decode that already started. Expired work produces no transcript or translation and records channel, model or intended provider, expiry reason, and queue wait. Cloud STT behavior is unchanged.

## REQ-013 — Shared-GPU scheduling and order

Capture, VAD, and admission for self and peer remain parallel. When both channels use GPU, their accepted work enters a single non-preemptive schedule ordered by `speech_end`; only one inference runs at a time against the shared model. After pending expiry is applied, retained jobs complete in that global FIFO order without channel priority. Switching providers does not create an end-to-end ordering guarantee outside the shared GPU schedule.

## REQ-014 — Established utterance-duration policy

The GPU provider must not introduce a hidden provider-specific utterance-duration cap. Peer VAD retains its seven-second maximum segment and self VAD retains no established maximum segment. The 12-second pending TTL is queue age, not utterance duration.

## REQ-015 — Backend and RTF diagnostics

Every CPU or Vulkan decode attempt that starts must produce a diagnostic record containing channel, actual resolved model, execution backend as `CPU` or `Vulkan`, input-audio seconds, decode seconds, `rtf = decode_seconds / audio_seconds`, and result. Queue wait and model load or warmup time must not be included in RTF and must be recorded separately when relevant. Failed started attempts retain these timing fields. CPU Auto records its actual resolved model. Work expired before decode records expiry and queue wait without an RTF value.

## REQ-016 — Localized and safe user feedback

Every new user-facing provider name, description, setup state, progress state, warning, failure, mismatch, repair, device, and retry message must be localized at the Flet UI boundary with locale-bundle parity. UI async actions must remain non-blocking. Diagnostics must not include raw audio, transcript text, translation text, credentials, secrets, or personal identifiers.

## REQ-017 — Windows native composition

The packaged Windows application must include the independently launchable GPU worker and preserve the existing overlay binary and startup contract. The Windows overlay and GPU worker must both be rebuilt for the candidate package, and installer smoke evidence must exercise the packaged composition with an alternate AppId and isolated installation directory.

# Protected Invariants

## Product invariants

### INV-P-001 — Peer chatbox isolation

Peer utterances and their derived text never route to the VRChat chatbox.

### INV-P-002 — Product-channel separation

Self, peer, and system outputs remain separate product channels through STT, translation, presentation, and diagnostics.

### INV-P-003 — Application availability under model failure

CPU asset partial failure and ordinary GPU worker, Vulkan, device, or OOM failure do not prevent the application from installing, starting, or using unaffected providers and features.

### INV-P-004 — No silent provider substitution

The product performs no model or provider substitution except the explicitly approved persistent manual-language mismatch switch to local Qwen3-ASR 0.6B.

### INV-P-005 — Explicit GPU resource consent

The GPU model is not downloaded or installed before explicit user opt-in, and saved inactive GPU settings do not consume GPU model memory.

## Durable architecture invariants

### INV-A-001 — Settings compatibility and recoverability

Existing settings and provider identities remain loadable, new settings round-trip with safe defaults, and no forward migration rewrite occurs before a recoverable backup exists.

### INV-A-002 — Separate GPU fault boundary

GPU model operations remain in a separately supervised Rust process that is independent of both the Python application and the overlay executable.

### INV-A-003 — Explicit lifecycle ownership

The application owns channel admission and routing; the worker owns GPU execution. Every long-running task and process has bounded cancellation, awaited shutdown or forced termination, and safe diagnostics.

### INV-A-004 — Single shared GPU execution resource

Concurrent self and peer GPU usage shares one device and model and serializes inference; it does not duplicate model residency or concurrently execute against the shared model.

### INV-A-005 — Overlay compatibility

The existing overlay protocol, executable identity, startup contract, and lifecycle remain compatible and independent of GPU worker availability.

### INV-A-006 — Verified model authority

A model is usable only when its own pinned installation contract validates; another model's validity cannot mask or satisfy it.

### INV-A-007 — Safe diagnostics

Required lifecycle, queue, backend, and RTF evidence is observable without recording raw audio, transcripts, translations, credentials, secrets, or personal identifiers.

### INV-A-008 — Provider and prompt compatibility

Established provider aliases and prompt fallback behavior remain accepted and observably equivalent after the provider surface expands.

### INV-A-009 — Awaited provider teardown

Provider replacement, channel disablement, and application shutdown await provider `close()` and do not abandon provider-owned work or process resources.

# Approved Decisions

- CPU Auto requires all three independently valid CPU models; direct valid CPU models remain usable under partial failure.
- Automatic routing follows pinned official capability lists rather than inferred language families.
- Existing `local_qwen` self and peer selections migrate to CPU Auto after backup, while manual Qwen selection continues to persist as `local_qwen`.
- Manual local-model language mismatch persistently switches to Qwen3-ASR 0.6B and does not auto-restore.
- GPU is a separate, explicitly installed strict-Vulkan provider with one global device selection and one shared loaded model.
- GPU execution always uses a separate lazy persistent Rust worker; it is not conditional on low VRAM and is not combined with the overlay.
- Worker residency follows active GPU-channel count with no idle timeout and immediate terminal shutdown after the last channel is disabled.
- Current available VRAM is a soft signal; actual load and warmup determine availability.
- GPU failure has no automatic CPU fallback or automatic retry.
- Local CPU and GPU pending work uses a 12-second `speech_end` TTL with no count cap; active decode is not TTL-cancelled.
- Shared GPU inference uses non-preemptive global `speech_end` FIFO without self or peer priority.
- Existing VAD duration behavior is preserved without a GPU-specific utterance cap.
- CPU and Vulkan attempts expose backend identity, resolved model, and decode-only RTF diagnostics.
- AC-009 does not require a new low-available-VRAM VRChat session; retained physical coexistence evidence plus automated low-reported-VRAM controls and deterministic OOM injection verify the unchanged advisory-VRAM and failure behavior.

# Open Product Decisions

None.

# Acceptance Criteria

| AC | Verifies | Evidence class | Required environment | Pass condition |
|---|---|---|---|---|
| AC-001 | REQ-001, REQ-002, INV-P-003, INV-A-006 | automated + production-composition + fault-injection | Windows `.venv`; candidate installer with alternate AppId and isolated installation directory | Independent expected-SHA success, mismatch, missing, stale, and download-failure states are exercised for all three CPU models; application installation always completes, CPU Auto is enabled only with three SHA-verified valid assets, and each valid direct model remains usable. |
| AC-002 | REQ-003, REQ-006, INV-P-004 | automated + comparative | Windows `.venv`; pinned official capability fixtures for all three CPU model revisions | Every capability-boundary language resolves to the required model; outside-union languages are not claimed by CPU Auto; a manual mismatch produces the localized notice, decodes through valid local Qwen, persists `local_qwen`, and does not auto-restore. |
| AC-003 | REQ-004, REQ-006, INV-P-003, INV-P-004, INV-A-006 | automated + UI integration + fault-injection | Windows `.venv` with independently missing and corrupt model fixtures | A directly selected invalid model remains selected, only its affected channel stops, no silent fallback occurs, and repair targets the exact model; invalid Qwen after mismatch leaves `local_qwen` selected and stops the channel. |
| AC-004 | REQ-005, INV-A-001 | automated + comparative + temporal | Windows `.venv`; representative supported existing settings files and keys, self-only, peer-only, both-channel, partial legacy, and current settings fixtures across two launches | Before rewrite a byte-identical recoverable backup exists; every existing `local_qwen` channel becomes CPU Auto; all other supported settings and keys remain readable and semantically equal; absent new fields receive safe defaults; canonical serialization reloads round-trip equivalent; a later manual Qwen choice serializes as `local_qwen`; and the second launch does not repeat migration. |
| AC-005 | REQ-007, REQ-016, INV-P-005 | automated + production-composition + manual | Fresh isolated packaged Windows installation with network observation, physical Vulkan device, and Flet UI | No GPU model request or asset appears before explicit opt-in; opt-in installs and validates Qwen3-ASR 1.7B GGUF Q6_K; the distinct GPU choice can be persisted independently for self and peer and resolves only to `transcribe.cpp` strict Vulkan; selecting it on either channel reveals one localized 1×1 device card with Auto and enumerated Vulkan devices shared across both channels; an unavailable saved device retains GPU selection and performs no CPU fallback. |
| AC-006 | REQ-008, REQ-016 | automated + temporal + manual UI | Packaged Windows application with fast, delayed-beyond-two-seconds, failed device-discovery, absent-model, unsupported-capability, and activation-failure controls | The first frame remains interactive, delayed discovery shows pending rather than failure, no model loads during discovery, and activation presents observably distinct missing-installation, unsupported-capability, validation, load, warmup, ready, and actionable activation-failure states. |
| AC-007 | REQ-009, REQ-011, INV-P-003, INV-A-002, INV-A-003, INV-A-005, INV-A-007 | automated + production-composition + fault-injection | Packaged Windows application, physical Vulkan device, Windows worker build, and injected load, warmup, OOM, device-loss, backend, and worker-crash failures | GPU discovery and `transcribe.cpp` strict-Vulkan model operations occur only in `PuriPulyHeartGpuWorker.exe`; authenticated local supervision reports lifecycle outcomes; every injected failure stops only GPU channels, retains GPU selection and the installed model, exposes manual retry, releases failed worker resources, and performs no CPU fallback or automatic retry while the application, CPU ASR, and overlay remain usable and diagnostics contain no prohibited content. |
| AC-008 | REQ-010, INV-P-005, INV-A-003, INV-A-004 | automated + temporal + process/memory observation | Packaged Windows application with saved-but-inactive, self-only, peer-only, both-channel, and shutdown scenarios on a physical Vulkan device | A saved inactive GPU selection starts no worker and loads no model; one worker and one model are resident while any GPU channel is active; disabling one of two consumers retains them; disabling the last rejects and discards new or pending work and cancels active work; bounded cancellation escalates to process termination; VRAM is released; and later activation performs a fresh load and warmup. |
| AC-009 | REQ-011, INV-P-003, INV-P-004 | automated + production-composition + fault-injection | Windows `.venv` low-reported-VRAM controls; retained supported Windows x64 physical-Vulkan and representative VRChat coexistence evidence; deterministic OOM injection | Injected low reported VRAM only warns and real load/warmup is attempted without proactive eviction; physical Vulkan composition confirms coexistence without proactive rejection; actual injected failure retains GPU selection and asset, exposes manual retry, performs no CPU fallback or automatic retry, and leaves unaffected application features usable. |
| AC-010 | REQ-012, REQ-013, REQ-014, INV-A-004 | automated + temporal + concurrency | Windows `.venv` with a controllable clock and decode worker; packaged physical-device confirmation for shared GPU serialization | Self and peer pending CPU/GPU jobs expire exactly from `speech_end` at 12 seconds only before start; expired work emits no transcript or translation and records channel, intended model or provider, expiry reason, and queue wait; active jobs survive TTL; no count cap drops work; cloud behavior is unchanged; retained shared-GPU jobs run one at a time in global `speech_end` order; and existing peer/self VAD duration behavior is unchanged. |
| AC-011 | REQ-015, INV-A-007 | automated + diagnostics inspection + comparative | Windows `.venv` CPU backends and packaged physical Vulkan backend with success, decode failure, and pre-start expiry cases | Every started attempt reports channel, resolved model, `CPU` or `Vulkan`, audio seconds, decode seconds, mathematically correct decode-only RTF, and result; queue/load time is separate; CPU Auto shows its resolved model; expired work reports channel, intended model or provider, expiry reason, and queue wait with no RTF and produces no downstream transcript or translation; no prohibited content appears. |
| AC-012 | REQ-016, INV-P-001, INV-P-002, INV-A-005, INV-A-008 | automated regression + manual UI | Windows `.venv` routing, provider-alias, prompt-fallback, and localization guards; packaged application in every supported locale; existing overlay production composition | Locale bundles remain in parity and every new state is localized; peer results never reach the VRChat chatbox; self, peer, and system remain separate; established provider aliases and prompt fallback produce their compatible behavior; and the existing overlay starts, authenticates, presents, and shuts down unchanged with GPU available, unavailable, and failed. |
| AC-013 | REQ-017, INV-A-002, INV-A-005 | build + production-composition | Windows Rust build; candidate packaged application and alternate-AppId isolated installer smoke | Windows overlay and GPU worker rebuild successfully; the package contains separate runnable native executables; the overlay preserves its established startup contract; and the isolated installer can install, launch, exercise CPU and GPU composition, and uninstall without confusing the two process identities. |
| AC-014 | INV-A-003, INV-A-009 | automated + temporal + lifecycle fault-injection | Windows `.venv` provider replacement, channel disablement, worker-cancellation, and application-shutdown controls | Provider `close()` is awaited before replacement or shutdown completes; cancellation and close failures are observed through the established lifecycle path; and terminal completion leaves no provider-owned task, queue consumer, model, or worker process running. |

# Decision Authority

## Executor may decide

- reversible implementation details
- private types and internal APIs
- file and helper placement
- implementation sequence
- exact authenticated local IPC framing
- exact log syntax and casing while preserving required semantic fields
- tests and diagnostics beyond the required acceptance evidence

## Independent review required

- durable boundary reliance
- production cutover
- legacy, compatibility, migration, rollback, or fallback path removal
- persistence, security, lifecycle, concurrency, or public API change
- material strategy pivot
- terminal completion

## User decision required

- observable product behavior
- scope or non-goal
- compatibility break
- irreversible migration
- security posture
- supported platform or provider
- required evidence weakening

# Completion Rule

Every acceptance criterion must be directly proven in its required environment and evidence class. Automated tests alone cannot replace Windows platform, packaged production-composition, physical Vulkan, VRChat coexistence, comparative, temporal, documentary, or manual evidence.
