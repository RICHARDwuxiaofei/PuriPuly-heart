## Verification

- Level: L0
- Scope: sync overlay two-slot design spec with main-worktree commit `d91578b3a154057152667c94b49d5aa54e0499a3`

### Commands run

1. `git show --stat --summary --oneline d91578b3a154057152667c94b49d5aa54e0499a3`
   - PASS
2. `git diff d91578b3a154057152667c94b49d5aa54e0499a3^1 d91578b3a154057152667c94b49d5aa54e0499a3 -- src/puripuly_heart/ui/controller.py src/puripuly_heart/app/wiring.py src/puripuly_heart/core/runtime/peer_channel.py src/puripuly_heart/core/stt/controller.py tests/ui/test_controller_branch_paths.py tests/core/test_peer_channel_routing.py tests/core/test_peer_channel_runtime.py`
   - PASS
3. `sed -n '1,340p' docs/superpowers/specs/2026-04-04-overlay-two-slot-static-caption-design.md`
   - PASS
4. `rg -n 'TBD|TODO|FIXME|XXX|Similar to Task' docs/superpowers/specs/2026-04-04-overlay-two-slot-static-caption-design.md`
   - PASS
5. `rg -n 'provider replacement|emit all logically publishable|Stop owning physical visibility trimming|Own the only two-slot visible set|Ghosting and damage-band cleanup stay out of scope' docs/superpowers/specs/2026-04-04-overlay-two-slot-static-caption-design.md`
   - PASS

### Outcome

- PASS

### Skipped

- No executable code tests were run because this change updates the design spec only.

### Notes

- The spec now distinguishes overlay runtime teardown/full clear from peer STT provider swap and peer runtime policy changes.
- The spec now distinguishes overlay restart with preserved presenter state from explicit disable/shutdown full-clear behavior.
- The spec now assigns capped logical visible-set ownership to the presenter and physical slot continuity to native state.
- The spec now includes the contract-version bump requirement and minimum accent-aware damage clearing requirement.
