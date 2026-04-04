## Verification

- Level: L0
- Scope: align two-slot static overlay spec with peer subsystem rewrite assumptions

### Commands run

1. `sed -n '1,260p' /mnt/c/Users/salee/Documents/dev/puripuly_heart/docs/superpowers/specs/2026-04-04-peer-subsystem-rewrite-design.md`
   - PASS
2. `sed -n '260,520p' /mnt/c/Users/salee/Documents/dev/puripuly_heart/docs/superpowers/specs/2026-04-04-peer-subsystem-rewrite-design.md`
   - PASS
3. `rg -n "peer|translation|utterance_closed|PeerTranscriptFinal|TranslationFinal|peer.*overlay|show_peer_original" /mnt/c/Users/salee/Documents/dev/puripuly_heart/src/puripuly_heart/core/orchestrator /mnt/c/Users/salee/Documents/dev/puripuly_heart/src/puripuly_heart/core/overlay /mnt/c/Users/salee/Documents/dev/puripuly_heart/tests/core -g '!**/__pycache__/**'`
   - PASS
4. `rg -n "TBD|TODO|FIXME|XXX" docs/superpowers/specs/2026-04-04-overlay-two-slot-static-caption-design.md`
   - PASS
5. `sed -n '1,280p' docs/superpowers/specs/2026-04-04-overlay-two-slot-static-caption-design.md`
   - PASS

### Outcome

- PASS

### Skipped

- No executable code tests were run because this change only updates design documentation.

### Notes

- The overlay spec now explicitly treats first-visible peer translation as new occupant assignment.
- The overlay spec now states that peer runtime restart or deactivation affects slots only through the existing hub overlay event contract.
