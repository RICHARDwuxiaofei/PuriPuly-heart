---
id: PRD-VNEXT-STRANGLER-A2-001
status: reviewed
source: .agents/specs/prd/drafts/vnext-strangler-refactor-a2.source.r5.md
baseline_ref: vnext@2a2a35af442c2b6414e0d7ff22b42c9a4b57c427
integration_target: vnext
document_review_verdict: ready
blocking_open_decisions: 0
---

# Outcome

PuriPuly Heart reaches a maintainable owner-based backend and a Flet 0.85 UI without sacrificing the current vnext product: every production responsibility is transferred incrementally behind the working UI, every transfer preserves verified Windows behavior except for the approved retirement of two user choices, and the final fixed-size UI preserves the verified baseline appearance and interaction exactly at 1136 by 850 except for the removal of the Fast Translation and Integrated Context settings cards and selection UI before the legacy UI, `GuiController`, unused failed-refactor UI residue, and multi-responsibility `ClientHub` ownership are retired from the release tree.

# Established Baseline

## Code baseline

- The normative starting code baseline is `vnext@2a2a35af442c2b6414e0d7ff22b42c9a4b57c427`.
- This commit is a baseline candidate, not yet a verified functional baseline. The first product gate must establish or repair and then record an exact commit that passes the required Windows production-composition baseline.
- Uncommitted working-tree changes present during PRD authorship are excluded from the baseline.
- The current production path combines the established Flet UI, `GuiController`, and `ClientHub` with selected settings, provider, runtime, output, lifecycle, and application assets already active in vnext.
- The reverted B0 integration and failed candidate UI are not part of the production baseline.
- G00 establishes the pre-change functional and visual baseline. The only authorized subsequent product deltas are the two retired choices and their UI specified by REQ-012 and REQ-013; all other comparative behavior remains anchored to the accepted G00 baseline.

## User-visible surfaces

- Main frameless Flet application shell, title bar, navigation, dashboard, logs, about surface, settings views, cards, controls, status displays, and all dialogs and modals.
- Self microphone capture, STT, transcription presentation, translation, and configured outputs.
- Peer audio capture, STT, translation, desktop or VR subtitle presentation, and peer-specific consent and state.
- Manual text translation and its loading, success, partial, empty, retry, and error states.
- Provider selection, managed authentication, API credential entry and verification, provider status, and fallback behavior.
- Settings editing, cancellation, persistence, restart restoration, migration, and SecretStore-backed credentials.
- VRChat chatbox output, desktop subtitle overlay, and native VR subtitle overlay.
- English, Japanese, Korean, Russian, and Simplified Chinese UI locales.
- Normal startup, background operation, fixed main-window presentation, shutdown, restart, and debug UI preview.

## Actual product entrypoints

- Packaged Windows executable `PuriPulyHeart.exe`, entering through `puripuly_heart.main` and launching the Flet GUI by default.
- Source entrypoints `python -m puripuly_heart.main` and `python -m puripuly_heart.main run-gui`.
- Desktop overlay entrypoints exposed through `puripuly_heart.main` for the production renderer, preview, and diagnostic repro.
- Separately built native Windows VR overlay launched through the established overlay startup protocol.
- There is no supported product headless entrypoint.

## Platform and environment

- Product and production-composition evidence run on supported Windows x64 systems.
- Automated Python evidence uses the repository `.venv` on Windows.
- The baseline GUI uses Flet 0.28.3. The replacement GUI targets the Flet 0.85 release line.
- Visual comparison uses identical Windows version, theme, display scale, locale, settings, data, window state, and content for baseline and replacement captures.
- Required main-window comparison uses only the established 1136 by 850 size. The replacement main window is fixed at that size; minimum-size behavior, user resizing, resize-responsive behavior, and rendering at other dimensions are outside the parity evidence contract.
- Native VR overlay evidence includes a rebuilt Windows overlay when its changed surface requires it. Installer smoke evidence uses an alternate AppId and isolated installation directory.
- Broker verification, when its changed surface requires it, runs only in a Linux-native workspace with Linux-installed dependencies.

## Compatibility baseline

- Existing product behavior and output semantics at the verified functional baseline are authoritative unless this PRD explicitly changes them. The main-window parity contract is the fixed 1136 by 850 presentation defined by REQ-008.
- Self, peer, and system are separate product channels, and peer utterances never route to the VRChat chatbox.
- Existing settings files and keys remain loadable, forward migration backs up before rewrite, persistence remains round-trippable, and defaults remain safe. Historical values retain their meaning except that `stt.low_latency_mode=false` and `ui.integrated_context_enabled=false` no longer restore retired choices and are normalized to the fixed policies in REQ-012 and REQ-013.
- Existing SecretStore service names and keys remain compatible. Secrets are loaded through SecretStore and encrypted-file storage continues to require `PURIPULY_HEART_SECRETS_PASSPHRASE`.
- Existing provider aliases, configured provider behavior, prompt fallback behavior, and current safe error-detail behavior remain compatible.
- Existing Broker `/v1` behavior, desktop overlay behavior, native overlay protocol and startup contract, packaged executable identity, installer identity, and user-data profile remain compatible.
- Debug UI preview remains explicitly gated and inert with respect to settings persistence, secrets, and external systems.

# Scope

## Included

- Establishing an exact, reproducible Windows functional baseline for the current vnext product before architecture cutovers proceed.
- Incremental transfer of coherent backend responsibilities from the current UI/controller/Hub path to explicit production owners with defined input, output, lifecycle, cancellation, shutdown, and diagnostics contracts.
- Continued use of active and correct vnext settings, provider, safety, runtime, output, overlay, and compatibility assets.
- A stable UI-facing application contract proven through the existing UI before replacement UI cutover.
- Migration of the UI runtime to the Flet 0.85 release line without redesigning the UI or changing observable product behavior.
- Exact visual, interaction, locale, settings, provider, audio, translation, output, overlay, lifecycle, packaging, and restart parity against the verified baseline at the fixed 1136 by 850 main-window size.
- Retirement of the Fast Translation and Integrated Context user choices, removal of their settings cards and selection UI, and safe normalization of their historical persisted values to the approved fixed policies.
- Evidence-gated retirement of the legacy UI, `GuiController`, and multi-responsibility application ownership in `ClientHub`.
- Evidence-gated removal from the final vnext release tree and package of dormant UI implementations, presentation wiring, components, assets, adapters, and tests created by the failed prior refactor that are not required by the verified production path and differ from the approved UI appearance or behavior.

## Non-goals

### NG-001 — B0 or candidate resurrection

Reactivating the reverted B0 integration, adopting the failed candidate UI, or continuing its horizontal layer-completion plan is not part of this contract.

### NG-002 — Return to dev

Resetting the product to `dev` or semantically backporting the vnext feature set to `dev` is not part of the selected strategy.

### NG-003 — Unapproved product redesign

Changing information architecture, visual design, copy, navigation, workflows, defaults, features, provider behavior, or output semantics beyond the two retired settings choices and UI surfaces explicitly defined by REQ-012 and REQ-013 is not authorized.

### NG-004 — Product headless mode

A supported headless microphone, stdin, translation, or release runtime is not introduced. Test-only drivers may use the production composition for evidence.

### NG-005 — Framework-driven behavior change

Flet 0.85 migration does not authorize visual or interaction differences at the fixed default size, backend contract redesign, or simultaneous backend responsibility changes.

### NG-006 — Premature legacy deletion

Legacy code is not removed merely because a replacement compiles, passes isolated tests, or exists behind test-only wiring.

### NG-007 — Replacement god object

Moving the current responsibilities into a differently named controller, hub, host, coordinator, service locator, or generic settings API without establishing single responsibility and explicit boundaries is not completion.

### NG-008 — Release authorization

PRD review and implementation completion do not authorize merge, push, deployment, public release, or worktree cleanup.

### NG-009 — Repository history rewriting

Removing failed-refactor UI residue from the final integration target does not require deleting historical commits or branches, rewriting Git history, or destroying archived evidence.

# Requirements

## REQ-001 — Functional baseline authority

Architecture cutovers must not proceed until an exact vnext commit passes the Windows production-composition baseline for startup, self STT and translation, peer STT and translation, channel-correct output, settings restoration, configured provider use, overlays, and clean shutdown. If the starting code baseline fails, restoration work must preserve established behavior and a new exact functional baseline must be recorded before cutovers continue.

## REQ-002 — Incremental production ownership

Each cutover must transfer one coherent responsibility to one explicit owner in the real product composition. The owner must define its inputs, outputs, state, failure behavior, diagnostics, and lifecycle obligations appropriate to that responsibility. Unrelated responsibilities and UI presentation must remain unchanged during that cutover.

## REQ-003 — Behavior-preserving cutover

Every owner cutover must preserve the verified baseline's user-observable behavior, data meaning, output routing, timing-sensitive interaction semantics, and failure containment except for the approved changes in REQ-012 and REQ-013. The old and new side-effecting paths must not both execute. The corresponding legacy responsibility remains available until the new owner is proven in production composition and may be removed only after the required evidence passes.

## REQ-004 — Stable UI-facing application boundary

Before replacement UI cutover, the existing UI must prove a stable application boundary in which UI code submits explicit user intents and renders localized view state or presentation events without owning provider creation, credential storage, settings persistence, capture or translation orchestration, output routing policy, or long-running backend task lifecycle.

## REQ-005 — Explicit lifecycle and diagnostics

Every async or long-running production responsibility must have an explicit owner, cancellation path, coordinated shutdown behavior, and actionable diagnostics. Provider replacement and application shutdown must await provider `close()` and must not leave active capture, translation, overlay, or provider tasks behind.

## REQ-006 — Compatibility preservation

Owner replacement and UI migration must preserve existing settings and persistence meaning except for the two retired values defined by REQ-013, and must preserve backup and migration safety, SecretStore compatibility, provider aliases, prompt fallback behavior, Broker `/v1`, overlay protocols and startup behavior, executable and installer identity, locale availability, and established user-data locations.

## REQ-007 — Isolated Flet migration

The Flet 0.85 UI must consume the UI-facing application boundary already proven through the baseline UI. A UI migration cutover must not introduce or complete a backend ownership transfer, and backend contract changes discovered during UI migration require separate review and evidence before the UI migration resumes.

## REQ-008 — Exact UI appearance and interaction parity

The replacement UI must be visually and interactively identical to the verified baseline UI at the established 1136 by 850 main-window size, not merely similar or functionally equivalent, except that the Fast Translation and Integrated Context settings cards and their selection UI are absent as required by REQ-013. The main window must remain fixed at 1136 by 850 and must not permit user resizing. Only the positioning and sizing of remaining cards in the directly affected settings rows may change as necessary to close the two vacated card positions. Outside that narrow layout exception, the UI must preserve the window shell, title bar, navigation, screen composition, control types, ordering, positioning, visibility, enabled and selected states, labels, localized text, icons, colors, typography, spacing, sizing, scrolling, focus behavior, modal stacking and dismissal, loading and error feedback, and established interaction behavior.

Parity applies to the main views, all remaining settings surfaces, all remaining established dialogs and modals, and the baseline states reachable in normal and explicitly gated debug-preview operation across every supported locale at 1136 by 850. The two removed controls must not remain as disabled controls, empty cards, or inactive placeholders; closure of their vacated layout may reposition or resize only the remaining cards in the directly affected rows, must preserve their relative order, established design language, content, and behavior, and must pass comparative product-owner review. Minimum-window-size behavior, resize-responsive behavior, and rendering or interaction at any other main-window dimension are outside this contract. Any other user-perceptible difference at the fixed size is a failed acceptance result unless the product owner explicitly approves it through a reviewed PRD revision.

## REQ-009 — Evidence-gated legacy retirement

The legacy UI and `GuiController` may cease to be production entrypoints only after Flet 0.85 visual and behavioral parity passes. `ClientHub` must no longer own multiple application responsibilities; any retained Hub must be independently reviewed as a thin compatibility or channel facade. Removal must not weaken compatibility, rollback diagnosis, or required evidence.

## REQ-010 — Production release composition

The final product must remain operable through the established packaged Windows executable and supported source GUI entrypoints, with desktop and native VR overlay integration, installer behavior, provider access, settings restoration, and shutdown behavior preserved. Test-only composition is not sufficient for terminal acceptance.

## REQ-011 — Failed-refactor UI residue removal

The final vnext release tree and packaged product must not retain or ship dormant alternative UI implementations, presentation wiring, components, assets, adapters, or implementation-coupled tests created by the failed prior refactor when they are outside the verified production path and differ from the approved baseline appearance or behavior. Such code must not be used as the basis of the Flet 0.85 UI merely because it reflects a newer architecture.

Before removal, reachability and compatibility evidence must distinguish failed-refactor residue from the working baseline UI, required application contracts, migration or compatibility paths, reusable non-UI assets, and evidence fixtures. Active or required behavior must not be removed by classifying it as residue.

## REQ-012 — Canonical Fast Translation and context policies

Fast Translation is the single enabled translation policy and is not a user-selectable mode. A historical value, restart, or compatibility path must not select the retired normal mode in resolved runtime configuration, STT orchestration, the active translation coordinator, provider verification, or provider construction. Real configured provider operation, verification, failure containment, established Qwen low-latency asynchronous selection, and established provider fallback behavior must continue to work without restoring an off choice.

Integrated context is the single preferred context policy and is not a user-selectable mode. When effective peer translation applies and eligible peer context is available, translation requests use integrated context. Whenever effective peer translation does not apply, or peer context is otherwise unavailable or inapplicable, the runtime automatically uses local context without failing, crossing channels, or exposing a user mode selector.

## REQ-013 — Retired settings UI and compatibility boundary

The Fast Translation and Integrated Context settings cards, their selection dialogs or modals, their user-facing selectable states, and any user interaction path capable of restoring either retired choice are absent in every supported locale. No disabled card, empty card, inactive placeholder, or alternate control may preserve either retired choice.

Historical settings containing `stt.low_latency_mode` or `ui.integrated_context_enabled` remain loadable. Their historical `false` values are accepted at the compatibility boundary but normalize to the fixed policies in REQ-012, cannot restore the retired choices after restart, and cannot flow into the product as user-selectable domain state. Any forward rewrite is backed up before mutation, failure-safe, and round-trippable in its canonical representation. Other historical settings and keys retain their established meaning.

# Protected Invariants

## Product invariants

### INV-P-001 — Peer chatbox denial

Peer utterances never route to the VRChat chatbox under success, partial, retry, fallback, error, or shutdown conditions.

### INV-P-002 — Channel separation

Self, peer, and system messages retain distinct identity, state, presentation, and output eligibility throughout capture, transcription, translation, fallback, and rendering.

### INV-P-003 — Exact UI preservation

The verified baseline UI appearance and interaction at the fixed 1136 by 850 main-window size are the authoritative replacement target except for the approved absence of the two retired settings cards and selection UI. No other redesign or unapproved visible deviation is permitted at that size.

### INV-P-004 — Core translation continuity

Self and peer capture, STT, translation, presentation, and configured output flows continue to work through the real Windows product composition after every relevant cutover.

### INV-P-005 — Persistence and credential continuity

Valid existing settings and credentials remain usable with the same meaning except for the two retired settings values explicitly normalized by REQ-013. Settings remain loadable and round-trippable, and forward migration remains backed up and failure-safe.

### INV-P-006 — Provider and prompt compatibility

Established provider aliases, configured provider selection, managed and user credential behavior, and prompt fallback semantics remain compatible.

### INV-P-007 — Inert debug preview

Debug preview remains explicitly gated and cannot persist settings, mutate secrets, or call external systems merely by exercising hidden UI states.

### INV-P-008 — Overlay and packaging compatibility

Desktop and native VR overlay behavior, native protocol and startup contract, packaged executable behavior, and installer safety remain compatible.

### INV-P-009 — Retired choices stay retired

Fast Translation remains enabled as the sole product policy, context remains integrated-preferred with automatic local fallback, and no UI or persisted historical value restores either retired user choice.

## Durable architecture invariants

### INV-A-001 — One production owner per responsibility

A production responsibility has one active owner and one side-effecting execution path at a time.

### INV-A-002 — Proof before removal

Existing production behavior is not removed until its replacement is active in production composition and has passed the required evidence.

### INV-A-003 — Explicit boundaries

Cross-boundary behavior uses explicit input, output, lifecycle, message, rendering, persistence, or provider contracts rather than hidden cross-layer state access.

### INV-A-004 — Owned concurrency

Every background task and external resource has an explicit owner, cancellation and shutdown path, and diagnostics.

### INV-A-005 — Presentation does not own application behavior

The final UI owns rendering, localization, transient presentation state, and user interaction only; it does not own backend orchestration, persistence, providers, output policy, or resource lifecycle.

### INV-A-006 — Migration isolation

Backend ownership cutover and Flet framework migration are separately reviewable changes with separate evidence.

### INV-A-007 — No redistributed monolith

Completion requires reduced responsibility concentration and dependency direction that can be enforced; distributing the same hidden ownership across generic services or a replacement hub is not sufficient.

### INV-A-008 — One shipped UI implementation

The final release tree and package contain one approved Flet 0.85 production UI path and no dormant alternative UI produced by the failed refactor.

# Approved Decisions

- A2 is the selected strategy: working vnext preservation with incremental internal owner replacement.
- The current vnext production UI/controller/Hub path remains the behavior oracle until replacements are proven.
- The reverted B0 integration and failed candidate UI are excluded.
- Existing active and correct vnext foundations are retained rather than rewritten by default.
- Flet 0.85 migration follows a proven UI-facing application boundary and remains separate from backend ownership changes.
- The Flet 0.85 UI must preserve the verified baseline appearance exactly except for the approved removal of the Fast Translation and Integrated Context settings cards and selection UI; no other UI redesign is authorized.
- The Flet 0.85 main window is fixed at 1136 by 850 and is not user-resizable. Minimum-size and alternate-size rendering are not parity commitments.
- Fast Translation is fixed on as the sole product policy and is no longer user-selectable.
- Context is fixed to an integrated-preferred policy with automatic local fallback when eligible peer context is unavailable or inapplicable, and is no longer user-selectable.
- The two retired settings cards and their selection UI are removed in all five locales without inactive placeholders; every other fixed-size UI surface remains under exact parity.
- Historical values for the two retired choices remain loadable but normalize to the fixed policies through a backed-up, failure-safe, round-trippable compatibility boundary.
- The legacy UI and controller are removed only after replacement parity, and ClientHub's multi-responsibility ownership is eliminated.
- Unused UI code created by the failed prior refactor is also removed from the final vnext release tree and package after reachability and compatibility review proves it is not required production, migration, compatibility, reusable non-UI, or evidence code.
- There is no supported product headless runtime.

# Open Product Decisions

None.

# Acceptance Criteria

| AC | Verifies | Evidence class | Required environment | Pass condition |
|---|---|---|---|---|
| AC-001 | REQ-001, INV-P-001 through INV-P-008 | automated + production-composition + manual | Clean worktree at an exact commit, repository `.venv`, supported Windows x64, configured real audio and providers, established desktop and VR output environment | The exact commit and environment are recorded; the application starts; self and peer STT and translation complete; outputs retain channel eligibility; peer never reaches chatbox; settings survive restart; configured providers and overlays operate; shutdown completes; every failure is repaired and the full matrix rerun before the functional baseline is accepted. |
| AC-002 | REQ-002, REQ-003, INV-A-001, INV-A-002 | automated + production-composition + comparative + manual | Exact functional baseline and cutover commits on supported Windows x64 using the same scenario data and configured dependencies | For every owner cutover, traceable production wiring proves the new owner is active, only one side-effecting path executes, focused contracts and affected architecture guards pass, and the before/after Windows scenario has no unapproved behavior, state, output, failure, or timing-semantic difference beyond the deltas authorized by REQ-012 and REQ-013 before legacy responsibility removal. |
| AC-003 | INV-P-001, INV-P-002, INV-P-004 | automated + production-composition + fault-injection | Repository `.venv` and supported Windows x64 across self, peer, system, partial, fallback, retry, error, and shutdown scenarios | Self, peer, and system identity remain distinct at every observed boundary; peer output is denied to the VRChat chatbox in every scenario; allowed desktop and VR presentation still occurs exactly once. |
| AC-004 | REQ-004, INV-A-003, INV-A-005, INV-A-007 | automated architecture + production-composition + independent review | Repository source and tests at the proposed UI-boundary commit, plus the existing Windows UI using that boundary | Dependency enforcement and independent review find no UI ownership of providers, credentials, persistence, capture or translation orchestration, output policy, or backend task lifecycle; the baseline UI exercises all required commands and presentation states through the boundary in production composition. |
| AC-005 | REQ-005, INV-A-004 | automated + temporal + production-composition + fault-injection | Repository `.venv` and supported Windows x64 with active capture, provider calls, overlays, cancellation, provider swap, and repeated shutdown/restart | Tasks and resources have observable owners and diagnostics; cancellation and shutdown complete within established baseline expectations; provider `close()` is awaited; no active owned task, audio stream, overlay session, or provider resource remains; failed replacement preserves a usable prior state. |
| AC-006 | REQ-006, INV-P-005, INV-P-006 | automated compatibility + production-composition + restart + migration | Historical supported settings fixtures, SecretStore test environments, configured providers, supported Windows x64, and Linux-native Broker workspace only if Broker changes | Settings round-trip and supported forward migration preserve meaning and backups except for the two explicitly normalized values in REQ-013; credentials remain available through compatible SecretStore keys; provider aliases and prompts resolve as before; restart restores the same state except that retired choices cannot return; any touched Broker or provider compatibility checks pass in their required environment. |
| AC-007 | REQ-007, INV-A-006 | comparative source review + automated + production-composition | Separate backend-boundary and Flet 0.85 cutover revisions, repository `.venv`, supported Windows x64 | The Flet cutover consumes the previously proven application boundary, its reviewed change contains no backend ownership transfer, backend contract changes are handled separately, and the real Windows UI reaches the same backend behaviors through that boundary. |
| AC-008 | REQ-008, INV-P-003 | comparative visual + comparative interaction + automated window-constraint inspection + manual product-owner review | Baseline Flet 0.28.3 and replacement Flet 0.85 on identical supported Windows x64 environment, theme, display scale, locale, settings, data, window state, and content; main window fixed at 1136x850 | Side-by-side and overlaid captures plus direct interaction comparison at 1136x850 cover the shell, every main view, every remaining settings surface, every remaining established dialog/modal, normal states, error/loading/disabled/selected states, gated debug-preview states, and all five locale bundles; the two retired cards and selection UI are absent without inactive placeholders; only remaining cards in the directly affected rows move or resize to close the vacancies, their relative order, design, content, and behavior remain unchanged, and the closure passes product-owner visual review; every surface and interaction outside that narrow geometry exception is identical; inspection and direct manipulation prove the replacement main window remains 1136x850 and cannot be user-resized. |
| AC-009 | REQ-009, INV-A-002, INV-A-005, INV-A-007 | automated architecture + production-composition + independent review + manual | Final proposed legacy-retirement revision on supported Windows x64 after AC-007 and AC-008 pass | The Flet 0.85 UI is the production entrypoint; the legacy UI and `GuiController` are unreachable and removable without regression; ClientHub has no multi-responsibility application ownership or is removed; dependency guards pass; the full Windows baseline matrix still passes after deletion. |
| AC-010 | INV-P-007 | automated + manual inertness | Supported Windows x64 with debug preview explicitly enabled and external calls, persistence, and SecretStore writes observable | Exercising every debug-preview state performs no settings persistence, secret mutation, provider, Broker, audio, chatbox, desktop overlay, or VR overlay external action. |
| AC-011 | INV-P-008 | automated protocol + production-composition + build + isolated installer smoke | Supported Windows x64, rebuilt native overlay when touched, packaged application, alternate installer AppId, isolated installation directory | Desktop and VR overlays retain protocol, startup, rendering, and shutdown behavior; the packaged app launches normally; isolated install, upgrade scenario when applicable, launch, and uninstall do not alter the production installation or user profile unexpectedly. |
| AC-012 | REQ-010, INV-P-004 through INV-P-008 | full automated regression + packaged production-composition + manual + temporal | Release-candidate packaged Windows application, supported configured providers, audio inputs, desktop and VR outputs, persisted settings and secrets, and established installer environment | The packaged application completes the full accepted baseline matrix, repeated restart and shutdown, settings restoration, provider use, self and peer translation, output routing, overlays, and exact UI comparison without relying on a test-only or headless runtime. |
| AC-013 | REQ-011, INV-A-008 | reachability analysis + source and package inventory + automated architecture + independent review + production-composition | Final vnext source tree, release-candidate package, exact accepted UI contract, and supported Windows x64 production composition after AC-008 passes | An inventory classifies every retained and removed candidate by production reachability, visual and behavioral conformance, compatibility, migration, reusable non-UI, and evidence purpose; every item proven to belong only to the visually or behaviorally incorrect failed-refactor alternative UI and proven unnecessary for production, compatibility, migration, reusable non-UI behavior, and evidence is absent from the release tree and package; required production paths, application contracts, compatibility and migration paths, reusable non-UI assets, and evidence fixtures and tests are preserved; architecture checks and the full Windows baseline still pass after removal. |
| AC-014 | REQ-012, INV-P-004, INV-P-009 | automated behavioral + traceable production wiring + production-composition + provider + fault-injection + manual | Historical `low_latency_mode=false` settings, repository `.venv`, and supported Windows x64 with real configured self and peer audio, configured Qwen and other supported provider paths as applicable, effective peer translation available and unavailable, restart, retry, fallback, and error scenarios | After loading the historical false value and restarting, traceable evidence proves the enabled policy reaches resolved runtime configuration, STT orchestration, the active translation coordinator, provider verification, and provider construction; Qwen selects the established low-latency asynchronous path rather than the retired synchronous normal-mode path; configured provider verification and translation work; effective peer translation with eligible context produces integrated-context requests; every scenario without effective peer translation, or with unavailable or inapplicable peer context, produces local-context requests without failure or channel mixing; established fallback and error containment remain effective; self and peer translation complete with peer chatbox denial preserved. |
| AC-015 | REQ-013, REQ-006, INV-P-003, INV-P-005, INV-P-009 | automated migration + persistence round-trip + restart + comparative visual and interaction + manual product-owner review | Historical settings fixtures containing both values of the retired keys, isolated settings copies, repository `.venv`, and supported Windows x64 at fixed 1136x850 across all five locales | Historical files load without error; a forward rewrite creates a backup before mutation and is failure-safe; both historical false values normalize to the fixed policies and cannot return after save and restart; the canonical representation round-trips while unrelated settings retain meaning; both cards and all selection UI are absent without inactive placeholders or stale localized choices; only remaining cards in the directly affected rows move or resize to close the vacancies, their relative order, design, content, and behavior remain unchanged, the closure is approved, and every surface outside that narrow geometry exception remains visually and interactively identical. |

# Decision Authority

## Executor may decide

- Reversible implementation details.
- Private types and internal APIs.
- File and helper placement.
- Implementation sequence within the currently authorized responsibility boundary.
- Focused tests, diagnostics, and evidence collection mechanics that do not weaken required evidence.

## Independent review required

- Durable boundary reliance.
- Production cutover.
- Legacy, compatibility, migration, rollback, or fallback path removal.
- Persistence, security, lifecycle, concurrency, or public API change.
- Any claim that a retained Hub or facade is thin rather than an application owner.
- Material strategy pivot.
- Terminal completion.

## User decision required

- Observable product behavior.
- Any visual or interaction difference from the verified baseline UI at the fixed 1136 by 850 main-window size.
- Scope or non-goal.
- Compatibility break.
- Irreversible migration.
- Security posture.
- Supported platform, locale, or provider.
- Required evidence weakening.

# Completion Rule

Every acceptance criterion must be directly proven in its required environment and evidence class. Automated tests, static architecture checks, screenshots, provenance records, or sub-issue completion alone cannot replace required Windows production-composition, comparative visual and interaction, provider, audio, overlay, installer, temporal, or manual evidence. PRD review and Goal completion remain separate from merge, push, deployment, release, and worktree cleanup approval.
