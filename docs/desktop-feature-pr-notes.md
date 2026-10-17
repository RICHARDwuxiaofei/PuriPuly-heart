# Desktop model assets and microphone diagnostics

This branch retains model management and microphone recording/playback for subsequent UI integration and polishing. It incorporates upstream `dev` at `196ddca3` without changing the existing settings or microphone-test layout in this integration.

## Model management

- Recognition and translation assets are selected independently. Opening the manager preselects assets for the configured local providers; installing an asset does not activate that provider.
- Explicit per-model source choices are task-scoped and do not silently fall back. Ordinary provisioning without an explicit choice retains the upstream source fallback policy.
- E4B and 26B use their own current pinned manifests. E4B requires its target and MTP files; 26B requires its target file. Local import validates size and SHA-256 before atomic promotion and cannot replace a working installation with invalid input.
- Cancellation drains owned verification/download work before releasing staging resources. A model-manager cancellation cannot cancel an installation owned by another surface.
- Community-mirror reachability and complete multi-GB transfers are not established by local fixture checks.

## Microphone recording and playback

- The existing microphone-test owner retains bounded, in-memory raw and recognition-preprocessed PCM from the selected capture route.
- Recordings default to 10 seconds and are bounded to 30 seconds. Playback uses local output; no cloud recognition, automatic export, or gain change is required.
- Playback pauses capture through the application boundary. Generation and user-intent checks prevent restoring capture switches that the user changed while playback was running.
- Closing releases capture, playback, and recording buffers. Failures remain available through the normal logging owner after the dialog closes, without logging audio or credentials.

## Ownership and verification

See [architecture.md](architecture.md#desktop-diagnostic-surfaces) for ownership and [desktop-feature-test-results.md](desktop-feature-test-results.md) for current verification and limits. UI redesign is not part of this branch integration.
