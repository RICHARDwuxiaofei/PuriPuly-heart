# vNext Headless Runtime Removal Plan

Status: implemented
Date: 2026-07-02
Worktree baseline: `vnext-refactoring-architecture`, HEAD `bc4ba26`

## Summary

Headless functionality is still present in vNext. Most of it is legacy CLI runtime and tests, but one important function currently lives in a headless module while being used by the GUI runtime.

Search scope:

```text
headless | Headless | run-stdin | run-mic | osc-send
```

Pre-removal footprint:

```text
source: 8 files, 106 matches
tests:  11 files, 317 matches
```

The intended removal boundary is:

```text
runtime entrypoint / adapter wiring / CLI compatibility / tests-docs cleanup
```

## Current Headless Scope

### Fully headless source modules

These are direct deletion candidates:

```text
src/puripuly_heart/app/headless_stdin.py
src/puripuly_heart/app/headless_runtime_config.py
```

### Mixed headless and GUI-runtime code

This file needs a split before deletion:

```text
src/puripuly_heart/app/headless_mic.py
```

It contains removable headless runner pieces:

```text
HeadlessMicRunner
HeadlessMicInitializationError
_create_headless_llm_provider
_create_headless_vrc_osc_receiver
_headless_vrc_mic_receiver_runtime_diagnostics_sink
```

But it also contains the GUI-needed shared audio/VAD loop:

```text
run_audio_vad_loop
```

The GUI controller imports this function directly:

```text
src/puripuly_heart/ui/controller.py:6330
src/puripuly_heart/ui/controller.py:7460
```

Therefore `headless_mic.py` cannot be deleted until `run_audio_vad_loop` is moved into a proper core runtime module.

Recommended destination:

```text
src/puripuly_heart/core/runtime/audio_vad_loop.py
```

This is preferred over `core/audio/vad_loop.py` because the function is not just a low-level audio utility. It owns the runtime loop that connects:

```text
AudioSource frames
→ resample
→ VAD chunk
→ optional VRC mic audio gate
→ VadEventSink
```

## CLI Entrypoint Footprint

Headless commands live in:

```text
src/puripuly_heart/main.py
```

Current CLI subcommands:

```text
osc-send
run-stdin
run-mic
```

### `run-stdin`

This is fully legacy headless functionality.

Remove:

```text
run-stdin parser
_load_headless_stdin_runner()
HeadlessStdinRunner global cache
main() run-stdin branch
```

### `run-mic`

This is fully legacy headless functionality.

Remove:

```text
run-mic parser
--vad-model option
_load_headless_mic_types()
_requires_soxr_runtime_startup_check() run-mic check
main() run-mic branch
```

### `osc-send`

This is not named headless, but it is a GUI-less CLI output path. If the goal is to remove headless functionality completely, this should also be removed.

Risk: `osc-send` is used by the Windows release smoke test:

```text
scripts/ci/build-release-artifacts.ps1:534
```

Current script snippet:

```powershell
$smokeTest = Start-Process -FilePath $exePath -ArgumentList @("osc-send", "ci-smoke") -Wait -PassThru
```

That path is protected release infrastructure, so removing `osc-send` requires updating the release smoke and its guards.

Existing smoke coverage already includes:

```text
--version
local-qwen-runtime-check
soxr-runtime-check
```

Recommendation: remove `osc-send` too, and rely on the existing version/runtime/overlay startup checks rather than adding a GUI/Flet-dependent smoke.

## Public Compatibility Inventory

Headless is currently included in the public compatibility inventory.

```text
tests/config/compatibility_surface_inventory.json
```

Current public-ish exports:

```json
{"name": "headless_stdin", "kind": "submodule"}
{"name": "headless_mic", "kind": "submodule"}
```

The internal module classification also includes:

```text
puripuly_heart.app.headless_runtime_config
```

Removal therefore requires updating:

```text
src/puripuly_heart/app/__init__.py
```

Current `app.__all__`:

```python
__all__ = ["headless_stdin", "headless_mic", "wiring"]
```

Expected after removal:

```python
__all__ = ["wiring"]
```

This is an intentional compatibility surface removal and should be treated as such in review.

## Lifecycle Guard Cleanup

The lifecycle task guard currently has a headless allowlist entry:

```text
tests/architecture/test_lifecycle_task_guard.py
```

Current allowlist:

```python
("src/puripuly_heart/app/headless_stdin.py", ASYNCIO_CREATE_TASK): 1
```

Rationale:

```text
headless adapter owns its terminal flush loop until a headless runtime owner replaces the adapter-local queue drain
```

Deleting `headless_stdin.py` removes this lifecycle debt. The allowlist and rationale should be deleted in the same change.

## Release and Installer Impact

### Release script

Affected:

```text
scripts/ci/build-release-artifacts.ps1
tests/app/test_release_dependency_guards.py
```

Current release guard expects `osc-send`:

```python
assert "osc-send" in script
assert script.index("soxr-runtime-check") < script.index('"osc-send", "ci-smoke"')
```

If `osc-send` is removed, update the release script and guards together.

Suggested final release smoke coverage:

```text
--version
local-qwen-runtime-check
soxr-runtime-check
```

Do not add a GUI/Flet smoke unless it is known to be stable in Windows CI.

### Installer

No direct headless command reference was found in:

```text
installer.iss
```

The installer does reference the appdata path, but not `osc-send`, `run-stdin`, or `run-mic`.

## Documentation Impact

No active docs under `docs/*.md` directly referenced `headless`, `run-mic`, `run-stdin`, or `osc-send`.

Historical agent logs contain old references, but those are past verification records and should not be edited as part of this cleanup.

## Removal Plan

### Phase 1 — Extract shared VAD loop

Goal: remove GUI runtime dependency on `app.headless_mic` before deleting headless modules.

Create:

```text
src/puripuly_heart/core/runtime/audio_vad_loop.py
```

Move:

```text
headless_mic.run_audio_vad_loop
→ core.runtime.audio_vad_loop.run_audio_vad_loop
```

Update imports in:

```text
src/puripuly_heart/ui/controller.py
```

Also handle:

```text
tests/app/test_headless_mic_pipeline.py
```

This test is named headless but effectively covers the shared audio VAD loop. Rename or absorb it.

Suggested rename:

```text
tests/app/test_headless_mic_pipeline.py
→ tests/core/test_audio_vad_loop.py
```

Verification:

```powershell
./.venv/Scripts/python.exe -m pytest tests/core/test_streaming_resampler.py tests/ui/test_controller_branch_paths.py tests/architecture/test_dependency_boundaries.py --tb=short
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m black --check src tests
```

Suggested commit:

```text
refactor(runtime): move audio vad loop out of headless adapter
```

### Phase 2 — Remove headless modules and CLI commands

Delete:

```text
src/puripuly_heart/app/headless_mic.py
src/puripuly_heart/app/headless_stdin.py
src/puripuly_heart/app/headless_runtime_config.py
```

Delete tests:

```text
tests/app/test_headless_mic_runner.py
```

Update:

```text
src/puripuly_heart/main.py
src/puripuly_heart/app/__init__.py
src/puripuly_heart/app/wiring_llm_factory.py
```

Remove from `main.py`:

```text
HeadlessStdinRunner global
_load_headless_mic_types()
_load_headless_stdin_runner()
run-stdin parser and branch
run-mic parser and branch
osc-send parser and branch, if full removal is approved
_requires_soxr_runtime_startup_check() run-mic logic
```

Keep CLI commands:

```text
--version
run-gui
run-desktop-overlay
run-desktop-overlay-preview
local-qwen-runtime-check
soxr-runtime-check
```

Verification:

```powershell
./.venv/Scripts/python.exe -m pytest tests/app/test_main_cli.py tests/architecture/test_lifecycle_task_guard.py tests/config/test_public_compatibility_surfaces.py --tb=short
./.venv/Scripts/python.exe -m pytest --tb=short
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m black --check src tests
git diff --check
```

Suggested commit:

```text
refactor(app): remove legacy headless cli runtimes
```

### Phase 3 — Update release smoke if `osc-send` is removed

Modify:

```text
scripts/ci/build-release-artifacts.ps1
```

Remove:

```text
osc-send ci-smoke
```

Keep existing smoke coverage:

```text
--version
local-qwen-runtime-check
soxr-runtime-check
```

Verification:

```powershell
./.venv/Scripts/python.exe -m pytest tests/app/test_release_dependency_guards.py --tb=short
```

Suggested commit:

```text
test(release): drop headless osc smoke command
```

## Final Verification

After all phases:

```powershell
./.venv/Scripts/python.exe -m pytest --tb=short
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m black --check src tests
git diff --check
```

If `scripts/ci/build-release-artifacts.ps1` is modified, treat it as protected release-path work and make sure the release guard tests are part of the evidence.

## Recommended Final Scope

Remove all of the following:

```text
run-stdin
run-mic
osc-send
headless_stdin.py
headless_mic.py
headless_runtime_config.py
headless tests
headless public inventory entries
headless lifecycle allowlist
release osc-send smoke
```

Keep the shared audio loop, but move it out of the headless namespace:

```text
headless_mic.run_audio_vad_loop
→ core.runtime.audio_vad_loop.run_audio_vad_loop
```

## Final Assessment

Current vNext headless remains as:

```text
CLI commands:
  osc-send
  run-stdin
  run-mic

source modules:
  app/headless_mic.py
  app/headless_stdin.py
  app/headless_runtime_config.py

tests:
  about 11 files / 317 matches

GUI-required dependency:
  run_audio_vad_loop only
```

Removal difficulty is medium. The feature removal itself is straightforward, but two dependencies must be handled carefully:

1. `run_audio_vad_loop` must be promoted to a non-headless core runtime owner before deleting `headless_mic.py`.
2. `osc-send` is used by release smoke, so removing it requires protected release script and release guard test updates.

Implementation result: full removal was performed, including `osc-send` and the protected release smoke update.
