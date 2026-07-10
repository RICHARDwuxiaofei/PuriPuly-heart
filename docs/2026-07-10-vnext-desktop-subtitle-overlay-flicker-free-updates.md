## Metadata

- Title: vNext Desktop Subtitle Overlay Flicker-Free Updates
- Status: reviewed
- Generated: 2026-07-10
- Source: current conversation, repository inspection, and vNext architecture review
- vNext Baseline: ports/adapters boundaries, explicit lifecycle owners, centrally validated diagnostics, and target-neutral overlay compatibility
- Review Rounds: 4

## Problem Statement

When a Translation arrives, the Desktop Subtitle Overlay can briefly blink white or appear to flash before the new caption is shown. This is particularly disruptive during a conversation because a user sees the failure at the exact moment they need to read the new Translation.

The Desktop Subtitle Overlay receives complete, revisioned `OverlayPresentationSnapshot` values. Its rendering path reconstructs its entire Flet page content for accepted snapshots and for post-start runtime controls rather than retaining a stable caption surface and changing only the values that have changed. Consecutive self-channel or peer-channel Transcript and Translation updates can therefore cause repeated full visual-tree replacement. The product must update captions without exposing a white, blank, or visibly reconstructed window surface.

This vNext PRD applies only to the Desktop Subtitle Overlay target. It does not diagnose or change the separate VR Subtitle Overlay / SteamVR native renderer, including its presentation-refresh behavior. It preserves the target-neutral Overlay Presentation Snapshot, bridge, manifest, controller persistence, and `OverlayRuntimeHandle` compatibility surfaces.

## Solution

Keep one transparent Desktop Subtitle Overlay window and one stable caption-surface hierarchy alive for the renderer lifetime. Apply each newer Overlay Presentation Snapshot, every post-start runtime control, the local empty-state lock action, every explicit preview selection update, and preview keyboard return-to-edit action by updating bounded caption slots, line content, visual properties, control state, and visibility in place. Do not replace the page root, caption-surface hierarchy, or slot/line controls, and do not re-reveal the window after startup.

Snapshot delivery remains revisioned and full-state based. The desktop renderer collapses a contiguous run of already-pending snapshots to its newest valid revision before painting, because the newest complete snapshot supersedes earlier snapshots. Before the final paint, it advances required runtime-only visual history through every valid superseded snapshot so current grow-only card-width behavior is unchanged. Post-start runtime-control messages remain FIFO ordered and form batching barriers.

The result must preserve the existing Desktop Subtitle Overlay appearance and behavior: two visible caption slots, active/finalized mapping, text language/font policy, visual configuration, moving/edit mode, locked mode (`pass_through` is an internal control name), saved window bounds, and startup behavior. The implementation changes UI rendering, renderer lifecycle/scheduling, diagnostics, and diagnostic-CLI wiring only; it does not change settings ownership, output routing, or the VR Subtitle Overlay.

## User Stories

1. As a PuriPuly user, I want a Translation to replace or add caption text without a white flash, so that I can keep reading during a conversation.
2. As a PuriPuly user, I want the Desktop Subtitle Overlay window to remain visually stable while a new Spoken Turn is translated, so that the overlay does not feel like it is reopening.
3. As a PuriPuly user, I want a new self-Channel Translation to update the existing caption surface in place, so that the original Transcript and Translation transition cleanly.
4. As a PuriPuly user, I want a new peer-Channel Translation to update the existing caption surface in place, so that peer captions remain readable without a flash.
5. As a PuriPuly user, I want empty caption state to become transparent in pass-through mode without displaying an unintended white or opaque window, so that the overlay stays unobtrusive.
6. As a PuriPuly user, I want the configured Desktop Subtitle Overlay background opacity to remain unchanged while captions update, so that visual preferences are respected.
7. As a PuriPuly user, I want the caption window to stay in its current position and size during caption updates, so that it does not jump or reset while I speak.
8. As a PuriPuly user, I want locked/pass-through interaction to remain locked while captions update, so that the overlay does not unexpectedly capture my mouse.
9. As a PuriPuly user, I want moving/edit interaction to remain usable while captions update, so that I can continue positioning the overlay.
10. As a PuriPuly user, I want short and long Translations to retain the current dynamic card-width behavior without a visual reset between updates, so that captions do not jitter unnecessarily.
11. As a PuriPuly user, I want Korean, Japanese, Chinese, Latin, mixed-script, and emoji captions to retain their existing font policy when updated, so that all captions remain legible.
12. As a PuriPuly user, I want the Desktop Subtitle Overlay to show no more than its existing two selected caption slots, so that new rendering behavior does not change the information density.
13. As a PuriPuly user, I want the latest available caption state to appear promptly during a burst of updates, so that the overlay does not lag behind the conversation by rendering obsolete intermediate states.
14. As a PuriPuly user, I want a normal overlay startup to remain free of a startup flash, so that the update fix does not regress the existing startup experience.
15. As a PuriPuly maintainer, I want the Desktop Subtitle Overlay to retain a stable rendering root after startup, so that snapshot handling cannot repeatedly tear down the visible surface.
16. As a PuriPuly maintainer, I want each rendered desktop state to correspond to a complete Overlay Presentation Snapshot, so that a partial or mixed-turn caption is never shown.
17. As a PuriPuly maintainer, I want stale snapshot revisions to be ignored, so that delayed bridge delivery cannot overwrite a newer rendered state.
18. As a PuriPuly maintainer, I want contiguous pending snapshot bursts to use latest-state rendering while preserving runtime-only visual history from their superseded snapshots, so that redundant paint work does not change grow-only caption-card width behavior.
19. As a PuriPuly maintainer, I want runtime-control messages to preserve FIFO order, so that position, visual configuration, and interaction-mode changes stay reliable.
20. As a PuriPuly maintainer, I want runtime controls to form ordering boundaries around snapshot coalescing, so that a caption update cannot cross a user-visible configuration change.
21. As a PuriPuly maintainer, I want Desktop Subtitle Overlay detailed diagnostics to identify rendered revision, snapshot disposition, and safe visual state, so that future display complaints can be investigated without logging caption text or secrets.
22. As a PuriPuly maintainer, I want renderer failure and shutdown ownership to remain unchanged, so that fixing updates does not create orphaned Flet tasks or prevent clean overlay shutdown.
23. As a PuriPuly maintainer, I want the Desktop Subtitle Overlay bridge and launch contracts to remain compatible, so that existing installed versions and startup flows continue to work.
24. As a PuriPuly maintainer, I want automated tests to distinguish in-place updates from full visible-surface replacement, so that the white-flash regression cannot silently return.
25. As a PuriPuly maintainer, I want a manual Windows Desktop Subtitle Overlay verification procedure, so that transparent-window compositing is verified on the platform where the symptom occurs.
26. As a PuriPuly maintainer, I want visual configuration, bounds, and locked/edit controls to preserve the same root and caption-control identities after startup, so that a future non-snapshot update cannot reintroduce the flash.
27. As a PuriPuly maintainer, I want all Desktop Subtitle Overlay diagnostics and repro artifacts to pass vNext central validation and redaction, so that no Transcript, Translation, token, file path, stack trace, or raw payload is persisted or displayed.
28. As a PuriPuly maintainer, I want the installed application to expose explicit, non-UI diagnostic commands, so that the actual packaged Desktop Subtitle Overlay can be reproduced without starting providers or loading settings.
29. As a PuriPuly maintainer, I want the diagnostic runtime to own and await its bridge, renderer, backdrop, timer, and artifact lifecycle, so that a repro run cannot leak tasks or resources on completion, failure, or cancellation.
30. As a PuriPuly maintainer, I want the source-development and frozen-installed diagnostic commands to dispatch the same behavior, so that release verification is reproducible across both environments.
31. As a PuriPuly maintainer, I want the diagnostic command surface to remain separate from normal GUI and overlay startup flows, so that user-facing behavior and existing retained CLI commands remain unchanged except for the explicitly approved diagnostic commands.
32. As a PuriPuly maintainer, I want architecture-boundary tests to prove that the renderer does not gain controller persistence, settings, SecretStore, provider, broker, or output-routing dependencies, so that the vNext ownership model remains intact.
33. As a PuriPuly maintainer, I want CLI-dispatch tests to prove that the diagnostic commands stay lightweight and isolated, so that a simple QA action cannot initialize the full app or access secrets.
34. As a PuriPuly maintainer, I want regression tests to prove that no protocol, manifest, or SteamVR path changes are needed for this Desktop-only fix, so that target-neutral overlay compatibility is protected.
35. As a PuriPuly maintainer, I want terminal verification to treat a missing validated Windows capture as blocked evidence rather than success, so that the reported white-flash outcome is not overstated.
36. As a PuriPuly maintainer, I want empty-state lock and preview-selection updates to retain the same surface identities, so that debug and interaction paths cannot reintroduce the same white-flash failure.

## Implementation Decisions

- The changed boundaries are **UI rendering**, **renderer lifecycle/scheduling**, **message/diagnostics**, and **diagnostic-CLI wiring** for the Desktop Subtitle Overlay. Settings/persistence, Overlay Presentation Snapshot protocol, Overlay Bridge ordering, Translation behavior, output routing, and VR Subtitle Overlay rendering remain unchanged unless required to preserve this rendering contract.
- Scope is selected overlay target `desktop` only. The native SteamVR renderer, OpenVR texture submission, VR presentation-refresh bursts, and VR calibration behavior are explicitly excluded.
- The Desktop Subtitle Overlay owns one long-lived Flet page root and one long-lived transparent caption surface for a renderer process lifetime. No post-start snapshot, runtime-control operation, empty-state lock action, preview selection, or preview keyboard return-to-edit action may recreate this root, replace the visible page tree, or replace a bounded slot or line control.
- The caption surface has the existing bounded capacity of two presenter-selected slots. Slot controls and their primary/secondary line controls are retained and updated in place; unused slots are hidden or made transparent as appropriate.
- Snapshot application updates only the state needed to represent the newest complete Overlay Presentation Snapshot: slot identity, channel styling, active/finalized presentation, text, language/font selection, background visibility/opacity, and bounded card dimensions. Post-start visual configuration, window bounds, and locked/edit runtime controls also update their stable controls in place.
- The renderer must preserve current visual behavior: caption mapping, two-slot ordering, primary/secondary line limits, truncation, CJK/mixed-script font policy, shadows, colors, dynamic width growth policy, and transparent empty pass-through state.
- No post-start snapshot, runtime-control operation, empty-state lock action, preview selection, or preview keyboard return-to-edit action may change window visibility, always-on-top state, or window chrome; only a valid bounds control or preview size-preset selection may change bounds, and only a valid interaction control, lock action, or preview keyboard return may change locked/edit state. Preview size selection changes only its expected preview bounds while retaining the root, surface, slot, and line-control identities. Initial window reveal remains a startup-only operation.
- Existing edit/moving mode behavior remains intact, including its bounded empty-state lock action. The renderer updates stable controls in place and does not reinitialize interaction state while processing a snapshot or another post-start control.
- Snapshots remain full-state and revisioned. The desktop renderer accepts only a snapshot whose revision is newer than the last rendered or superseded revision; equal or older revisions are no-ops.
- Coalescing is required for a contiguous run of snapshot messages already pending when the desktop UI worker dequeues a snapshot. The worker drains immediately available following snapshot messages until the queue is empty or a runtime-control message is encountered; it does not wait or add a debounce delay for future messages.
- Within a drained snapshot batch, the renderer classifies snapshots strictly in FIFO delivery order and never sorts by revision. It advances the highest accepted revision immediately and advances required runtime-only visual history without painting superseded states. At minimum, the grow-only caption-card width floor must evolve exactly as it would if the snapshots had each been rendered. The newest valid snapshot is the only state painted for that batch.
- The diagnostic harness establishes deterministic coalescing cases with a harness-only renderer UI-queue ingress gate. After authenticated startup, it holds diagnostic snapshot dispatch, sends the complete scripted FIFO batch, confirms that the batch has entered the renderer queue, then releases the gate. The next scripted batch cannot begin until the harness receives a safe render-commit acknowledgement for the prior batch.
- The harness-only acknowledgement uses the existing renderer event envelope to carry synthetic revision and disposition fields to its local bridge endpoint. It is unavailable in normal application starts, does not change Overlay Bridge serialization or the public process-manager event contract, and contains no user-derived identifier or caption content.
- Runtime-control ordering requirements apply after the Flet page has started. Post-start runtime-control messages are never coalesced, dropped, or reordered. A runtime-control message is a barrier: pending snapshots before it may collapse to their newest revision, the control updates the retained surface in place, then later snapshots are considered separately.
- Startup runtime-control priming remains unchanged. In particular, the existing startup edit-mode behavior and its intentionally non-replayed initial interaction-mode control remain intact; this PRD does not change startup lock/pass-through semantics.
- Coalescing must not mutate the Overlay Bridge, alter message serialization, or change what the Overlay Presenter publishes. It is a desktop-render scheduling optimization only.
- Detailed diagnostics add safe revision-level observability for snapshot receipt, supersession, render start/commit, slot count, line count, surface visibility, interaction mode, and current window dimensions. Every renderer diagnostic and persisted structured repro artifact must pass the vNext central diagnostic validation/redaction boundary before it is written or displayed.
- Diagnostic event schemas use bounded allowlisted fields only. They must reject or redact raw caption text, user-derived snapshot/occupant/update identifiers, credentials, session tokens, file paths or contents, raw exception text, stack traces, and provider/broker payloads.
- Add two explicitly approved, non-UI installed-application subcommands: `PuriPulyHeart.exe run-desktop-overlay-repro` and `PuriPulyHeart.exe verify-desktop-overlay-repro`. Their source-development dispatch routes must execute the same behavior. Normal GUI, normal overlay-startup, and existing retained CLI command behavior remains unchanged.
- Windows no-flash acceptance may use the explicit source-development diagnostic commands when an installed build exposing the same commands is unavailable. Installed-command parity remains release verification work; neither the normal GUI nor rebuilding preview route is valid substitute evidence.
- `run-desktop-overlay-repro` starts the shipping Desktop Subtitle Overlay renderer through a local authenticated loopback bridge and a separate static non-white checkerboard backdrop. It accepts `--cycles`, `--dwell-ms`, and required `--output-dir`; defaults are 100 cycles and 150 milliseconds dwell so non-burst states are observable. `--cycles` must be an integer from `1` through `1000`; `--dwell-ms` must be an integer from `1` through `10000`. The output directory is created if absent, must be empty if it already exists, and fails with `invalid_argument` before startup if it is non-empty, invalid, unwritable, or either numeric argument is outside its range. Such preflight failure exits nonzero without creating JSONL or result artifacts; all later failures write a validated failure result.
- A dedicated diagnostic lifecycle owner owns the repro bridge, renderer, backdrop, timers, capture-artifact coordination, and shutdown. It freezes ingress, cancels and awaits owned tasks, closes resources, and emits validated diagnostics; no unmanaged task may be introduced for repaint scheduling or repro execution.
- `run-desktop-overlay-repro` writes centrally validated revision-level diagnostics to `<output-dir>/desktop-overlay-repro.jsonl` and a final `<output-dir>/result.json`. It exits with status 0 only after every scripted revision has been processed with its expected disposition, every expected paint-committed revision has rendered, and its owned resources have shut down cleanly; startup, bridge, render, timeout, validation, or cleanup failure writes a validated failure result and exits nonzero.
- Each JSONL record and the final result are flat, bounded structured diagnostic records validated/redacted through the central `persisted_logs` policy with diagnostic-only visibility before serialization. They may contain only allowlisted scalar fields. Disposition totals are flattened into scalar counts rather than serialized as nested maps.
- The JSONL artifact uses schema version 1 and contains one centrally validated `revision_outcome` record for each scripted revision. Each record contains only cycle, UTC wall-clock timestamp, monotonic milliseconds since harness start, synthetic harness revision, expected and actual disposition (`committed`, `superseded`, `stale`, or `failed`), and safe visual state (slot count, line count, surface visibility, interaction mode, window dimensions).
- The `result.json` artifact uses schema version 1 and records terminal outcome (`completed` or `failed`), `reason: null` for completed output, and classified failure reason (`invalid_argument`, `startup_failed`, `bridge_failed`, `render_failed`, `render_timeout`, `validation_failed`, `cleanup_failed`, or `artifact_invalid`) for failed output, plus requested/completed cycles, scalar committed/superseded/stale/failed counts, and renderer/bridge/backdrop shutdown completion.
- The MP4 is synthetic-fixture capture evidence, not a structured diagnostic event. The validator does not inspect or serialize its binary contents; it verifies only the expected capture name and non-empty presence, then emits centrally validated scalar artifact metadata that contains no caption text, user-derived identifiers, file path, or file content.
- `verify-desktop-overlay-repro --output-dir <directory>` is the second explicit diagnostic subcommand. It validates the required JSONL/result schemas through the same central validation policy, validates disposition counts and clean terminal state, and requires a non-empty `desktop-overlay-repro.mp4`; it exits nonzero with `artifact_invalid` when validation fails.
- The diagnostic subcommands are test/diagnostic surfaces only. They are unavailable through normal product UI, require explicit invocation, do not load or persist settings, do not load secrets, and make no provider, broker, output-routing, or external-network calls. Runs below 100 cycles are permitted for developer smoke diagnostics but are non-certifying; the artifact validator must reject them for the Windows no-flash acceptance procedure.
- The vNext `OverlayRuntimeHandle` and controller persistence ownership remain unchanged. The diagnostic CLI uses a separate owner and must not import controller persistence, settings mutation, SecretStore, provider, broker, or output-routing modules.
- No persisted setting, settings schema migration, Overlay Launch Manifest field, Overlay Bridge protocol field, or i18n copy change is required. The two explicit diagnostic subcommands are the only approved CLI-surface addition in this PRD.

### Normative Repro Batch Schedule

The harness begins from synthetic empty revision `0`. For cycle `c`, its revision base is `17 × (c - 1)` and every table revision `n` is emitted as `base + n`; revisions therefore never repeat across cycles. For each batch below, it holds the diagnostic-only ingress gate, writes the complete listed FIFO sequence, confirms queue ingress, releases the gate, and waits for the safe render-commit acknowledgement before beginning the next batch. The listed captions are synthetic local fixtures and never enter persisted diagnostics.

The harness uses a diagnostic-only raw ingress adapter instead of normal `OverlayBridge.replace_snapshot()` revision filtering. It emits the unchanged serialized snapshot envelope in FIFO order directly to the local authenticated renderer endpoint, including intentionally stale `10, 9, 11` cases. This adapter exists only for the explicit diagnostic command, never enters normal application starts, and does not change production Overlay Bridge serialization, monotonic filtering, or public protocol behavior.

| Batch | FIFO synthetic revisions | Required final state | Required dispositions |
| --- | --- | --- | --- |
| Self source | `1` | Active self-channel Transcript fixture | `1=committed` |
| Self Translation | `2` | Finalized self-channel Transcript plus Translation fixture | `2=committed` |
| Peer source | `3` | Peer source fixture | `3=committed` |
| Peer Translation | `4` | Finalized peer-channel Translation plus original Transcript fixture | `4=committed` |
| FIFO stale ordering | `10, 9, 11` | Final fixture at revision `11` | `10=superseded`, `9=stale`, `11=committed` |
| Width-history burst | `12, 13, 14` | Final short fixture at revision `14`, retaining the long fixture's width floor from `13` | `12=superseded`, `13=superseded`, `14=committed`; all three fixtures use identical block ID, occupant key, and appearance sequence |
| Two-slot replacement | `15` | Selected two-slot fixture | `15=committed` |
| Empty state | `16` | Transparent locked empty state | `16=committed` |
| Return caption | `17` | Caption fixture after empty state | `17=committed` |

One cycle therefore has exactly `9` committed, `3` superseded, `1` stale, and `0` failed outcomes. A run of `c` cycles must report `9c` committed, `3c` superseded, `c` stale, and `0` failed outcomes; the default 100-cycle run must report `900` committed, `300` superseded, `100` stale, and `0` failed outcomes. The verifier rejects missing, duplicate, reordered, out-of-range, or schedule-inconsistent synthetic revision records, any different disposition, or any missing render-commit acknowledgement.

### Normative Artifact Schema v1

Every persisted structured record is flat, scalar-only, centrally validated before serialization, and rejects unknown fields. The JSONL record allowlist is exactly:

| Field | Type / allowed values |
| --- | --- |
| `schema_version` | integer, exactly `1` |
| `record_type` | string, exactly `revision_outcome` |
| `cycle` | integer, at least `1` |
| `wall_clock_utc` | RFC 3339 UTC string |
| `monotonic_ms` | integer, at least `0` |
| `synthetic_revision` | integer, at least `0` |
| `expected_disposition`, `actual_disposition` | one of `committed`, `superseded`, `stale`, `failed` |
| `render_commit_acknowledged` | boolean; `true` exactly for the committed record of a batch after its acknowledgement is received, otherwise `false` |
| `slot_count` | integer from `0` through `2` |
| `line_count` | integer from `0` through `6` |
| `surface_visible` | boolean |
| `interaction_mode` | `edit` or `locked` |
| `window_width`, `window_height` | positive integer |

`result.json` is one flat scalar-only centrally validated object with exactly these fields: `schema_version` (integer `1`), `record_type` (`run_result`), `outcome` (`completed` or `failed`), `reason` (`null` when completed or the allowed failure reason when failed), `cycles_requested`, `cycles_completed`, `committed_count`, `superseded_count`, `stale_count`, `failed_count` (non-negative integers), and `renderer_shutdown_completed`, `bridge_shutdown_completed`, `backdrop_shutdown_completed` (booleans). Missing, extra, nested, non-scalar, or centrally rejected fields invalidate the artifact. The verifier requires exactly one `render_commit_acknowledged=true` record for each batch's committed outcome and rejects any missing or unexpected acknowledgement.

### Preview Bounds Contract

Preview startup bounds use `max(selected preset width, 1180)` by `max(selected preset height, 420)` so the preview stage is visible. A later preview size-preset selection changes window bounds only to that preset's configured width and height; fixture, background-surface, and background-alpha selections do not change bounds. Preview keyboard return-to-edit changes only interaction state. All of these updates retain the stable root, caption surface, slot controls, and line controls.

## Testing Decisions

- The highest-value automated seam is the Desktop Subtitle Overlay renderer window driven through its public startup, snapshot-dispatch, runtime-control, and shutdown operations with a Flet page test double that records visible-surface updates.
- Existing Desktop Subtitle Overlay renderer tests are the prior art for startup configuration, caption-plan mapping, interaction modes, detailed diagnostics, runtime controls, and Flet window behavior.
- Automated tests must verify externally observable renderer behavior rather than private helper structure or a particular source-file layout.
- Tests must show that after startup, a changed self-channel or peer-channel `OverlayPresentationSnapshot` updates the visible caption content while retaining the same page root, caption surface, slot controls, line controls, and window visibility state.
- The Flet page test double must assert that every post-start snapshot, visual-config control, bounds control, locked/edit control, empty-state lock action, preview selection, and preview keyboard return-to-edit action performs no page `clean` or `add` operation, retains root/slot/line-control identities, and performs no post-start write to `window.visible`, always-on-top, or window chrome. Bounds and interaction-mode changes are permitted only for their valid explicit control, lock action, preview keyboard return, or expected preview size-preset selection.
- Tests must cover the representative transition that triggered the report: a visible Transcript state followed by a finalized Translation state, including original and Translation lines where enabled.
- Tests must cover an empty-to-caption transition and caption-to-empty transition in locked mode, asserting that the output is transparent when empty and that no window visibility toggle occurs during normal snapshot handling.
- Tests must cover same-slot text replacement, slot replacement, one-slot/two-slot transitions, active-self/finalized transitions, and peer finalized Translation updates without recreating the visible root.
- Tests must verify that visual configuration, position/bounds, and locked/edit runtime controls continue to take effect in place, retain FIFO ordering when interleaved with snapshots, and never cause a post-start surface rebuild.
- Tests must verify latest-state behavior for a contiguous already-pending snapshot burst: only the highest valid revision is painted, its caption plan is exact, and older revisions cannot later overwrite it.
- Tests must verify FIFO delivery classification with the sequence `10 → 9 → 11`: revision 10 is superseded, revision 9 is stale, and revision 11 is committed; the drained batch is never revision-sorted.
- Repro-harness tests must prove that the diagnostic-only ingress gate has queued the entire scripted FIFO batch before release and that the prior render-commit acknowledgement is received before the next batch begins.
- Repro-harness tests must prove that the raw ingress adapter preserves the normal serialized snapshot envelope while bypassing monotonic filtering only in the diagnostic route; normal Overlay Bridge filtering and production protocol behavior remain unchanged.
- Repro-harness tests must execute every normative batch, assert its base-adjusted FIFO revisions, exact dispositions, and default 100-cycle totals, and fail when a revision record is missing, duplicated, reordered, out of schedule, or paired with a different disposition, acknowledgement, or final visual state.
- Coalescing tests must include a same-slot short → long → short sequence and prove that the final short caption retains the long caption's grow-only card-width floor, matching sequential rendering behavior even though the intermediate long state was not painted.
- Tests must verify that a runtime-control message separates coalescing groups, preserving the order of snapshots relative to that control.
- Tests must preserve startup priming semantics, including initial edit-mode behavior and the intentionally non-replayed initial interaction-mode control; post-start FIFO requirements must not be applied to startup priming.
- Tests must cover the empty-state lock action, each preview selector category, and preview keyboard return-to-edit action, proving their post-start display changes preserve the stable root, caption surface, slot/line controls, and protected window properties. The preview size selector must additionally prove that only its expected preview bounds change.
- Tests must verify stale/equal revision handling remains safe and does not repaint an older state.
- Tests must preserve existing coverage for caption mapping, two-slot limits, dynamic width growth, CJK and mixed-script font selection, transparent locked empty state, startup reveal, bounds handling, and lifecycle shutdown.
- Detailed-log and artifact tests must verify snapshot receipt, supersession, render start/commit, disposition, and safe visual metadata through central validation/redaction. They must reject raw caption text, user-derived IDs, file paths/contents, raw exception text, stack traces, manifest secrets, and provider/broker payloads.
- Repro-harness tests must verify deterministic revision dispositions, versioned artifact schemas, validation failure classification, source/frozen CLI dispatch parity, no-settings/no-secrets/no-network/no-output-routing isolation, nonzero failure exit behavior, and owned bridge/renderer/backdrop/timer shutdown. Artifact-validator tests must verify success for a complete output directory and failure for missing, empty, malformed, unsafe, unknown-field, nested-field, non-scalar, or disposition-inconsistent artifacts.
- CLI tests must verify required output-directory preflight: an absent directory is created, an empty existing directory is accepted, and non-empty, invalid, or unwritable directories exit nonzero with no JSONL/result artifacts.
- CLI tests must verify `--cycles` and `--dwell-ms` range preflight, default values, no-artifact invalid-argument exits, and that fewer than 100 completed cycles remain non-certifying for Windows no-flash acceptance.
- CLI tests must verify that the two explicit diagnostic subcommands are present, dispatch through lightweight imports, remain unavailable through normal product UI, and do not change existing normal GUI, normal overlay, preview, or runtime-check command behavior.
- Architecture-boundary tests must prove that the renderer and diagnostic owner do not gain controller persistence, settings mutation, SecretStore, provider, broker, or output-routing imports, and that overlay protocol, bridge, manifest, and SteamVR paths remain unchanged.
- Manual Windows QA is required because automated Flet doubles cannot prove real transparent-window compositor output. The required acceptance route is `.venv\Scripts\python.exe -m puripuly_heart.main run-desktop-overlay-repro --cycles 100 --dwell-ms 150 --output-dir <empty-output-directory>`, followed by the analogous `verify-desktop-overlay-repro` command. The installed-application commands remain the release-parity route when available, but their absence does not block this source-development acceptance procedure. The rebuilding `--preview` route is not valid evidence.
- The repro command must apply the normal post-start pass-through control, open a static high-contrast blue/magenta checkerboard backdrop behind the overlay, and repeatedly drive these deterministic complete-snapshot sequences: self active Transcript → self finalized Transcript plus Translation; peer source state → peer finalized Translation plus original Transcript; same-slot short → long → short burst; two-slot replacement; caption → empty; and empty → caption. It must exercise at least 100 repeated update cycles.
- Before running the command, start an OBS Studio Display Capture recording for the monitor containing the backdrop and overlay, configured for 60 FPS or higher, and save it to a sibling staging path outside `<empty-output-directory>`. Run the repro command, stop and finalize OBS after the command exits, then move the completed recording to `<empty-output-directory>/desktop-overlay-repro.mp4`.
- Run `.venv\Scripts\python.exe -m puripuly_heart.main verify-desktop-overlay-repro --output-dir <empty-output-directory>` after moving the completed capture. Pass only if the validator succeeds and frame-by-frame review of `desktop-overlay-repro.mp4` finds no non-empty update exposing a white or blank full-window surface, reopening/revealing the window, changing bounds outside a scripted bounds control, changing locked/edit state outside a scripted interaction control, or changing other protected window properties. The displayed final caption content and grow-only width behavior must match the final scripted snapshot. Any such frame, missing artifact, command failure, validator failure, or unresolved revision diagnostic is a failure.
- SteamVR/HMD QA is not part of this PRD's acceptance because the selected Desktop target does not use the native renderer or its presentation-refresh bursts.

## Out of Scope

- Changes to the VR Subtitle Overlay / SteamVR native renderer.
- Native D3D11 texture buffering, OpenVR compositor synchronization, placement/calibration submissions, or presentation-refresh burst behavior.
- Changes to Translation quality, Translation Connection selection, provider I/O, prompt policy, Transcript generation, or low-latency merge semantics.
- Changes to the Overlay Presenter selection rules, snapshot payload schema, Overlay Bridge protocol, launch manifest, or normal GUI/overlay/preview startup command behavior.
- New Desktop Subtitle Overlay styling, animations, caption slots beyond two, caption content policy, or user-facing settings.
- Persisting new operational state or changing existing Desktop Subtitle Overlay position, visual, or lock-setting semantics.
- General Flet framework upgrades or replacement of the Desktop Subtitle Overlay with a native DirectComposition renderer.
- CLI additions beyond the explicitly approved `run-desktop-overlay-repro` and `verify-desktop-overlay-repro` diagnostic subcommands.
- Any change to persisted settings, controller persistence ownership, `OverlayRuntimeHandle`, SecretStore keys, provider/broker behavior, output routing, or user-facing product copy.
- A guarantee that unrelated operating-system or GPU-level composition faults cannot flash; this work removes the application's identified full-surface rebuild trigger and verifies the result on Windows.

## Further Notes

- The observed white flash has a strong code-path correlation with whole-page rebuilding, but a captured compositor frame from a live Desktop Subtitle Overlay session is not yet available. The requirement is therefore framed as a user-visible no-flash outcome and includes Windows manual QA.
- The working repository also contains a separate SteamVR target and native renderer. Their rendering diagnosis must remain separate from this Desktop Subtitle Overlay PRD to avoid applying VR-specific fixes to the Flet path.
- Overlay Presentation Snapshots are complete display state, not deltas. This makes latest-state coalescing safe only within an uninterrupted already-pending snapshot run and only after revision validation. Runtime-only visual history still has to process superseded snapshots in order to preserve sequential visual behavior.
- The required Windows repro harness is an internal diagnostic/testing surface, not a user-facing preview or production feature. It must remain explicitly opt-in and isolated from settings, secrets, and providers.
- The high-contrast backdrop and saved 60-FPS video make a transient white full-window frame visually distinguishable from intended white caption text and retain a correlatable artifact with the revision-level renderer diagnostics.
- The renderer remains persistence-free. User intent and persisted Desktop Subtitle Overlay configuration continue to be owned by the parent application; the renderer applies current runtime state only.
- vNext requires diagnostic sinks to use bounded structured data and central validation/redaction. The repro artifact contract therefore deliberately uses synthetic harness revisions rather than user-derived overlay identifiers and never writes caption content.
- The retained-surface decision applies to all post-start updates, including caption snapshots, runtime controls, the empty-state lock action, preview selections, and preview keyboard return-to-edit. Startup priming is explicitly excluded and preserves the existing edit-mode/non-replayed interaction-control behavior.
