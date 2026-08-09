from dataclasses import dataclass

LOCAL_QWEN_GPU_06_MODEL_ID = "qwen3-asr-0.6b-q6-k-transcribe-vulkan"
LOCAL_QWEN_GPU_17_MODEL_ID = "qwen3-asr-1.7b-q6-k-transcribe-vulkan"
LOCAL_QWEN_GPU_MODEL_ID = LOCAL_QWEN_GPU_17_MODEL_ID
DEFAULT_LOCAL_QWEN_GPU_MODEL_ID = LOCAL_QWEN_GPU_17_MODEL_ID


@dataclass(frozen=True, slots=True)
class LocalGpuModelCatalogEntry:
    model_id: str
    display_label: str
    manifest_relative_path: str
    install_dirname: str
    gguf_filename: str
    engine: str
    quantization: str


LOCAL_GPU_MODEL_CATALOG = {
    LOCAL_QWEN_GPU_06_MODEL_ID: LocalGpuModelCatalogEntry(
        model_id=LOCAL_QWEN_GPU_06_MODEL_ID,
        display_label="Qwen3 ASR 0.6B",
        manifest_relative_path=("data/models/qwen3-asr-0.6b-q6-k-transcribe-vulkan.manifest.json"),
        install_dirname=LOCAL_QWEN_GPU_06_MODEL_ID,
        gguf_filename="Qwen3-ASR-0.6B-Q6_K.gguf",
        engine="transcribe.cpp-vulkan",
        quantization="Q6_K",
    ),
    LOCAL_QWEN_GPU_17_MODEL_ID: LocalGpuModelCatalogEntry(
        model_id=LOCAL_QWEN_GPU_17_MODEL_ID,
        display_label="Qwen3 ASR 1.7B",
        manifest_relative_path=("data/models/qwen3-asr-1.7b-q6-k-transcribe-vulkan.manifest.json"),
        install_dirname=LOCAL_QWEN_GPU_17_MODEL_ID,
        gguf_filename="Qwen3-ASR-1.7B-Q6_K.gguf",
        engine="transcribe.cpp-vulkan",
        quantization="Q6_K",
    ),
}


def local_gpu_model_catalog_entries() -> tuple[LocalGpuModelCatalogEntry, ...]:
    return tuple(LOCAL_GPU_MODEL_CATALOG.values())


def is_local_gpu_model_id(model_id: object) -> bool:
    return isinstance(model_id, str) and model_id in LOCAL_GPU_MODEL_CATALOG


def require_local_gpu_model(model_id: str) -> LocalGpuModelCatalogEntry:
    try:
        return LOCAL_GPU_MODEL_CATALOG[model_id]
    except KeyError as exc:
        raise ValueError(f"unsupported local GPU ASR model: {model_id}") from exc
