from __future__ import annotations

import ast
from pathlib import Path


def test_ui_does_not_construct_replace_or_close_self_stt_provider() -> None:
    source = Path("src/puripuly_heart/ui/controller.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "create_stt_backend" not in source
    assert "self_audio_lifecycle=" not in source
    assert "self_ingress=" not in source
    assert "application_runtime_host.compose" not in source
    rebuild = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_rebuild_stt_provider"
    )
    rebuild_source = ast.get_source_segment(source, rebuild) or ""
    assert "ManagedSTTProvider(" not in rebuild_source
    assert "replace_stt_provider" not in rebuild_source
    assert ".close(" not in rebuild_source


def test_application_host_service_depends_on_ports_not_concrete_adapters() -> None:
    source = Path("src/puripuly_heart/app/services/application_runtime_host.py").read_text(
        encoding="utf-8"
    )

    assert "app.wiring" not in source
    assert "app.adapters" not in source
    assert "ClientHub" not in source
    assert "SoundDeviceAudioSource" not in source
    assert "lambda config: None" not in source


def test_production_self_channel_uses_real_non_ui_audio_adapters() -> None:
    source = Path("src/puripuly_heart/app/adapters/application_runtime_production.py").read_text(
        encoding="utf-8"
    )

    assert "source_type: object = SoundDeviceAudioSource" in source
    assert "VadGating(" in source
    assert "run_audio_vad_loop(" in source
    assert "SelfSTTChannelOwner(" in source
    assert "determine_self_mic_capture_channels(" in source
    assert "DiagnosticAudioSource(" in source
    assert "audio_gate=self.audio_gate" in source
    assert "ProductionAudioRuntimeHooks" in source
    assert "extra_fields_provider=" in source
    assert "RuntimeResourceReplacementPlan(\n            directive.llm" in source
    assert "puripuly_heart.ui" not in source
