from __future__ import annotations

from puripuly_heart.core.vad.bundled import ensure_silero_vad_onnx


def test_ensure_silero_vad_onnx_copies_file(tmp_path):
    target = tmp_path / "silero.onnx"

    path = ensure_silero_vad_onnx(target_path=target)
    assert path == target
    assert path.exists()
    assert path.stat().st_size > 0

    same = ensure_silero_vad_onnx(target_path=target)
    assert same == target
