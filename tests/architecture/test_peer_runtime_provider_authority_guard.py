from __future__ import annotations

import ast
from pathlib import Path


def test_peer_channel_runtime_cannot_construct_replace_or_close_stt_provider() -> None:
    path = (
        Path(__file__).parents[2]
        / "src"
        / "puripuly_heart"
        / "core"
        / "runtime"
        / "peer_channel.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_attributes = {
        "replace_peer_stt_provider",
        "start_peer_stt_provider_ingress",
        "close_backend",
    }

    assert "stt_factory" not in source
    assert "freeze_for_provider_replacement" in source
    assert "resume_after_provider_replacement" in source
    assert "_retired_peer_providers" not in source
    assert not {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
    }
