# ADR: vNext Settings Intent and State Destinations

- Status: Accepted
- Date: 2026-06-08
- Work ref: `add-vnext-settings-schema`

## Context

The vNext settings schema separates persisted user intent from persisted
operational state. Order 2 classified two legacy UI fields as requiring a
decision before the vNext write path can be accepted:

- `ui.peer_translation_eula_accepted`
- `ui.integrated_context_bootstrapped`

The integrated-context enabled preference also needs a canonical home because it
is currently stored near the bootstrap flag as `ui.integrated_context_enabled`.

## Decision

Use a state + intent split:

```text
ui.peer_translation_eula_accepted -> state.peer_translation.eula_accepted
ui.integrated_context_enabled     -> intent.integrated_context.enabled
ui.integrated_context_bootstrapped -> state.integrated_context.bootstrapped
```

Peer translation EULA acceptance is persisted operational consent state. It is
not a runtime active flag and is not itself a user preference to enable peer
translation.

Integrated-context enabled is persisted user intent. It represents the user's
preference to use the feature.

Integrated-context bootstrapped is persisted operational state. It records that
the one-time setup/bootstrap path has already been completed or acknowledged.

## Consequences

- The vNext schema and migration must preserve these values under the paths
  above.
- The vNext serializer must not write the legacy `ui.*` projection for these
  values once the vNext schema is active.
- Runtime-only active state remains outside settings; these persisted facts do
  not directly represent running tasks, provider sessions, or UI snapshots.
- Future UI/service code should route mutations through the intent/state schema
  rather than reintroducing broad UI-owned settings fields.

## References

- `docs/superpowers/specs/2026-06-08-vnext-refactoring-architecture-design.md`
- `.agents/bundles/vnext-refactoring-architecture.yaml`
- `src/puripuly_heart/config/settings.py`
- `.agents/ledgers/vnext-refactoring-architecture-ledger.yaml`
