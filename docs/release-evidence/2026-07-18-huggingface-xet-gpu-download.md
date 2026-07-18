# Hugging Face/Xet GPU model download evidence

Date: 2026-07-18
Platform: Windows 11 10.0.22631, CPython 3.12.10

## Frozen distribution

- PyInstaller 6.17.0 completed from `build.spec` after rebuilding the Windows overlay and GPU worker and preparing the system-linked soxr runtime.
- `PuriPulyHeart.exe hf-xet-runtime-check` exited 0.
- Packaged `hf_xet/hf_xet.pyd` size: 9,393,664 bytes.
- Pre-change distribution artifact: 434,223,659 bytes and 339 files.
- Xet distribution: 443,742,447 bytes and 398 files.
- Measured delta: +9,518,788 bytes, +2.192%.
- The pre-change artifact came from the main checkout and predated this worktree. It is a practical artifact baseline rather than a clean rebuild of commit `7eae312a6e4fe53a59a3cea6eb580ef90b6ef179`.

## Frozen Xet and cancellation smoke

Command:

`python scripts/release/huggingface-xet-frozen-smoke.py --executable dist/PuriPulyHeart/PuriPulyHeart.exe --output-dir build/frozen-xet-smoke-comparison`

- Xet fixture: `Qwen/Qwen3-Reranker-0.6B` at `e61197ed45024b0ed8a2d74b80b4d909f1255473`, `tokenizer.json`.
- The Hub HEAD response exposed Xet hash `6aec39639a0a2d1ca966356b8c2b8426a484f80ff80731f44fa8482040713bdf`.
- Downloaded and validated 11,422,654 bytes with SHA-256 `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`.
- Frozen Xet elapsed time: 2.594 seconds; 4.199 MiB/s.
- `HF_XET_HIGH_PERFORMANCE` was absent.
- App-owned `.cache` metadata was absent after the download.
- The global Hugging Face hub cache remained at 1 byte and retained its 2025-07-30 timestamp; the global Xet cache remained at 3,956,602 bytes and retained its 2026-07-18 08:43 timestamp.
- Cancellation targeted the approved 1.69 GB GGUF, observed an established network connection in packaged helper PID 32856, then terminated it.
- No helper process or continuing network owner remained after the frozen cancellation.
- Focused installer/runtime tests separately proved late callback suppression and no promotion after failure or cancellation.

The complete machine-readable smoke report is generated at `build/frozen-xet-smoke-comparison/report.json`.

## Throughput comparison

The same clean local target and 11,422,654-byte object were downloaded with the existing direct httpx pattern after the Xet run:

- Frozen Xet: 4.199 MiB/s.
- httpx: 5.126 MiB/s.
- Order: Xet, then httpx.

This small-file sample does not demonstrate a speedup, so no speedup is claimed. A clean-cache comparison of the full 1.69 GB production GGUF remains the performance benchmark gap.
