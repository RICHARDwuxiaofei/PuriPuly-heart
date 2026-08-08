# Qwen3 ASR GPU device selection

The local Qwen3-ASR GPU provider discovers Vulkan devices when the application starts. The GPU device selector is in **General settings** and applies to both local Qwen3-ASR channels.

- **Auto** lets the Vulkan worker choose a usable device.
- Selecting a listed device requests that exact Vulkan device ID.
- The runtime compares the requested ID with the device reported by the worker. If they differ, startup fails instead of silently using another GPU.

For a support report, include the `[GPU ASR]` log entries for `device_discovered`, `activation_ready`, or `activation_failed`. They include the requested and actual device IDs, Vulkan registry index, device name and description, type, memory values, model-load duration, and warmup duration. Do not include API keys or uploaded audio.

The 0.6B local Qwen3-ASR provider uses its sherpa-onnx runtime and is CPU-based in this application. The Vulkan selector controls the GGUF GPU provider only.
