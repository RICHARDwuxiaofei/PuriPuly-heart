# Overlay Finding Fix Verification

- Verification level: `L2`
- Outcome: `PASS`

## Commands Run

```bash
direnv exec /mnt/c/Users/salee/Documents/dev/puripuly_heart/.worktrees/overlay-two-slot-static-caption env PYTHONPATH=. uv run pytest tests/core/test_overlay_presenter.py -q
```

- Result: `PASS` (`23` tests)

```bash
direnv exec /mnt/c/Users/salee/Documents/dev/puripuly_heart/.worktrees/overlay-two-slot-static-caption env PYTHONPATH=. uv run --with flet --with pytest --with pytest-asyncio --with pytest-cov python -m pytest tests/ui/test_controller_branch_paths.py -k "overlay_toggle_off_sends_shutdown_event_before_teardown or overlay_restart_reuses_presenter_scene_for_new_bridge or explicit_overlay_disable_resets_presenter_scene_for_next_session" -q
```

- Result: `PASS` (`3` tests)

```bash
cargo test --manifest-path native/overlay/Cargo.toml --test state --test runtime --test renderer -- --nocapture
```

- Result: `PASS`

```bash
cargo test --manifest-path native/overlay/Cargo.toml -- --nocapture
```

- Result: `PASS`
  - `src/lib.rs`: `23` passed
  - `tests/renderer.rs`: `27` passed
  - `tests/runtime.rs`: `32` passed
  - `tests/state.rs`: `8` passed

## Notes

- Fixed `active_self -> finalized` metadata handoff so `appearance_seq` and first-visible TTL anchor survive promotion.
- Initial runtime snapshot seeding now avoids replaying accent pulses on restart/reconnect, while later new occupants still trigger the pulse.
- Accent chrome now stays inside the slot left padding area, and renderer regression coverage compares accented vs plain damage bounds instead of assuming left-edge equality.

## Skipped

- Windows headset/manual smoke validation was not run in this WSL environment.
