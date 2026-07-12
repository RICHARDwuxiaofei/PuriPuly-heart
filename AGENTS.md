## Authority

- Do not add code comments unless explicitly requested.
- If resuming `/implement-work`, reload its skill and validate the ledger, selected source, hashes, worktree identity, and recorded next action before continuing.

## Product Invariants

- Peer utterances must never route to the VRChat chatbox.
- Keep self, peer, and system outputs as separate product channels.
- Preserve established compatibility contracts unless the task explicitly approves a breaking change.

## Rules by Changed Surface

- When changing Python behavior, identify the owning boundary and prefer explicit DTO, port, adapter, lifecycle, output, message, or renderer contracts over cross-layer imports.
- When changing settings or persistence, keep serialization round-trippable, preserve old settings files and keys, provide defaults, and back up persisted data before forward migration.
- When changing credentials, preserve SecretStore key compatibility and load secrets through SecretStore; encrypted-file storage requires `PURIPULY_HEART_SECRETS_PASSPHRASE`.
- When adding async or long-running work, keep I/O async and provide an explicit owner, cancellation, shutdown, and diagnostics path; provider teardown must await `close()`.
- When changing Flet UI, use i18n for user-facing text, keep locale bundles in parity, localize at the UI boundary, and use `page.run_task` for async callbacks.
- When changing debug preview behavior, keep it behind the explicit debug flag and prevent it from persisting settings, mutating secrets, or calling external systems.
- When changing providers or prompts, preserve provider aliases and prompt fallback compatibility; verify prompt behavior in `src/puripuly_heart/config/prompts.py`.
- When changing orchestrator defaults, verify their current values in `src/puripuly_heart/core/orchestrator/hub.py`.
- When changing the Broker, preserve `/v1` compatibility and run Node verification only in a Linux-native workspace using Linux-installed dependencies.
- When changing the Rust overlay, preserve its protocol and startup contract and finish by recompiling the Windows overlay.
- When smoke-testing the installer, use an alternate AppId and an isolated installation directory.

## Verification Environment

- Use `.venv` on Windows and `.venv-wsl` on Linux/WSL when present.
- In WSL, use `direnv exec <repo> ...` or `UV_PROJECT_ENVIRONMENT=.venv-wsl`.
- Select focused checks from the changed surface, then run broader checks when the affected contract crosses boundaries.
