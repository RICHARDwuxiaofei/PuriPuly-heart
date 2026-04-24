# Debug UI preview mode design

## Status

- Draft
- Conversation-approved direction as of 2026-04-24

## Context

Several UI states in PuriPuly are hard to reproduce through normal manual QA because they depend on broker/OpenRouter responses or near-exhausted managed entitlements.

Examples include:

- Dashboard managed trial card states
- managed usage/exhausted display
- `managed_release.brake` transient notice
- `managed_release.revoked_contact` transient notice
- founder-letter handoff dialog
- OpenRouter PKCE failure snackbar

These states are already testable through unit/controller tests, but visual verification is still inconvenient. A debug-only preview mode should allow manual QA to surface these UI states without forcing real broker, secret, or OpenRouter conditions.

## Design context

This feature follows the repository design context:

- Users normally need a warm, fast, low-friction VRChat translation UI.
- Debug preview controls are for developers/QA only and must not affect the normal product experience.
- The preview affordance should be visually small, clearly temporary/debug-only, and hidden unless explicitly enabled.

## Goals

1. Let developers manually preview hard-to-reproduce UI states from the real running GUI.
2. Keep preview mode completely hidden during normal app execution.
3. Avoid mutating persisted settings, secrets, broker state, or provider state.
4. Use existing view/controller methods where possible so previewed UI matches production rendering.
5. Document the existence and operating constraints of debug preview mode in `AGENTS.md`.

## Non-goals

- Building a full Storybook-style component gallery.
- Adding a permanent Debug tab to the product navigation.
- Simulating every broker/provider state in the first version.
- Persisting debug scenarios across app restarts.
- Making debug preview available to normal users.
- Replacing automated tests for hidden UI states.

## Entry point

Debug preview mode is enabled only by an explicit CLI flag.

Proposed CLI shape:

```bash
puripuly-heart run-gui --debug-ui-preview
```

If feasible with the current parser shape, the default GUI launch may also accept the same flag:

```bash
puripuly-heart --debug-ui-preview
```

The implementation should support both of these GUI launch forms if the parser structure allows it without awkward special cases:

```bash
puripuly-heart --debug-ui-preview
puripuly-heart run-gui --debug-ui-preview
```

The first form covers the normal no-command GUI launch. The second form covers explicit GUI launch. The core rule is fixed: preview controls must not appear unless an explicit debug flag is enabled.

## UI surface

### Floating debug button

When debug preview mode is enabled, the app shows one small floating button over the GUI.

Suggested label:

- `DBG`

Placement:

- floating over the app shell
- low visual priority
- top-right by default, offset below the title bar and away from the bottom navigation
- if implementation discovers this conflicts with existing hit targets, use the nearest non-blocking right-side corner

Behavior:

- clicking `DBG` toggles a compact popover panel
- the button and popover are never created in normal mode

### Popover panel

The popover is a compact list of preview actions. It should be small enough to stay out of the way while still being usable for QA.

First-version actions:

1. `Managed normal`
   - Show the Dashboard managed trial card.
   - Use a representative remaining value, e.g. `62%`.
   - Clear transient notice.

2. `Managed exhausted`
   - Show the Dashboard managed trial card.
   - Set remaining to `0%`.
   - Clear transient notice.

3. `Brake notice`
   - Show the Dashboard managed trial card.
   - Show `managed_release.brake` as transient notice.
   - Use a representative remaining value if needed.

4. `Revoked notice`
   - Show the Dashboard managed trial card.
   - Show `managed_release.revoked_contact` as transient notice.
   - Use a representative remaining value if needed.

5. `Founder letter`
   - Open the existing founder-letter dialog.
   - Do not change managed entitlement settings or mark real preview state as persisted.

6. `PKCE failure`
   - Show the existing `openrouter.pkce.failed` snackbar.
   - Do not launch browser PKCE.

7. `Clear preview`
   - Hide the Dashboard managed trial card.
   - Clear transient preview notice.
   - Close or leave alone any dialog according to existing UI behavior; do not force-close unrelated user dialogs.

## State and side-effect rules

Preview actions must be side-effect-light.

They must not:

- write `settings.json`
- mutate `SecretStore`
- call broker endpoints
- call OpenRouter endpoints
- launch the PKCE browser flow
- alter managed entitlement identity
- mark the founder letter as seen for a real entitlement

Preview actions may:

- call view methods such as `DashboardView.set_managed_trial_state(...)`
- open existing UI surfaces such as `FounderLetterDialog`
- show existing snackbars
- update in-memory visual state for the current GUI session

## Architecture

### CLI plumbing

`main.py` should parse a debug preview flag for GUI execution and pass it to the GUI entrypoint.

Expected flow:

```text
main.py parser
  -> main_gui(..., debug_ui_preview=True)
  -> TranslatorApp(..., debug_ui_preview=True)
  -> optional debug preview control is mounted
```

The exact function signatures should follow existing app patterns.

### App-level preview owner

`TranslatorApp` should own the debug preview surface because it has access to:

- `view_dashboard`
- `view_settings`
- snackbar helper
- founder-letter dialog plumbing
- current page/root layout

The first version should avoid adding debug responsibilities to `GuiController` unless a preview action genuinely needs controller-only behavior.

### Debug component boundary

Prefer a small dedicated component/module for the floating button and popover rather than scattering controls through `TranslatorApp`.

Possible file:

- `src/puripuly_heart/ui/components/debug_preview_panel.py`

The component should receive callback functions for each action. It should not know how to mutate Dashboard or app state directly.

Example boundary:

```python
DebugPreviewPanel(
    on_managed_normal=...,
    on_managed_exhausted=...,
    on_brake_notice=...,
    on_revoked_notice=...,
    on_founder_letter=...,
    on_pkce_failure=...,
    on_clear=...,
)
```

## AGENTS.md update

Add a concise repository policy note that debug UI preview mode may exist and how agents should treat it.

Intended guidance:

```md
- Debug UI preview mode may exist for hard-to-reproduce UI states.
  - Verify the exact CLI flag and preview actions in code before use.
  - Preview actions must not persist settings, mutate secrets, or call external providers/brokers.
  - Use preview mode for manual QA of hidden UI states instead of forcing real broker/OpenRouter states.
  - Debug preview controls must remain hidden unless the explicit debug flag is enabled.
```

The note should stay concise and policy-oriented, not a long usage manual.

## Testing expectations

Implementation should add focused tests for:

1. CLI/parser behavior
   - debug flag is accepted for GUI launch
   - flag defaults to disabled

2. App wiring
   - when disabled, no debug preview control is mounted
   - when enabled, the floating debug preview control is mounted

3. Preview actions
   - `Managed normal` calls Dashboard managed trial state with visible `True` and representative percent
   - `Managed exhausted` calls Dashboard managed trial state with `0%`
   - `Brake notice` uses `managed_release.brake`
   - `Revoked notice` uses `managed_release.revoked_contact`
   - `Founder letter` calls the existing founder-letter show path
   - `PKCE failure` shows the existing failure snackbar and does not launch PKCE
   - `Clear preview` clears the Dashboard managed trial preview state

4. Side-effect safety where practical
   - preview actions do not call secret storage or broker/OpenRouter clients
   - preview actions do not save settings

## Manual QA expectations

After implementation, a developer should be able to run:

```bash
puripuly-heart run-gui --debug-ui-preview
```

Then use the floating `DBG` button to visually inspect:

- Dashboard managed trial card normal state
- exhausted state
- brake transient notice
- revoked transient notice
- founder-letter dialog
- PKCE failure snackbar

## UI text and i18n

Debug preview controls are developer-only, but they still render as Flet UI controls. To stay aligned with repository policy, visible preview labels should use i18n keys rather than raw strings.

Suggested key family:

- `debug_preview.button`
- `debug_preview.managed_normal`
- `debug_preview.managed_exhausted`
- `debug_preview.brake_notice`
- `debug_preview.revoked_notice`
- `debug_preview.founder_letter`
- `debug_preview.pkce_failure`
- `debug_preview.clear`

Initial English-like labels are acceptable across locale bundles because this is an explicit developer/QA mode, but the keys must exist in every touched locale bundle.

## Acceptance criteria

- Debug preview controls are invisible unless the explicit debug flag is enabled.
- The floating `DBG` button opens a compact popover with the seven first-version actions.
- Each action previews the intended hidden UI state without external calls or persistence.
- Founder-letter preview uses the existing founder-letter dialog implementation.
- Dashboard managed trial previews use the existing Dashboard managed trial card rendering.
- `AGENTS.md` documents the debug preview policy.
- Automated tests cover disabled/enabled wiring and each preview action.
