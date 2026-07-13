from __future__ import annotations

import ast
from pathlib import Path


def test_ui_does_not_own_openrouter_pkce_resources() -> None:
    root = Path(__file__).resolve().parents[2]
    forbidden = {
        "OpenRouterPKCEClient",
        "_openrouter_pkce_client",
        "run_desktop_flow",
        "_create_callback_listener",
        "callback_origin",
    }
    for relative in ("src/puripuly_heart/ui/app.py",):
        source = (root / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert not forbidden.intersection(names), relative


def test_production_pkce_composition_is_outside_ui_package() -> None:
    root = Path(__file__).resolve().parents[2]
    production = (root / "src/puripuly_heart/app/adapters/openrouter_pkce_production.py").read_text(
        encoding="utf-8"
    )
    assert "OpenRouterPkceOwner" in production
    assert 'callback_origin="http://localhost:3000"' in production
    assert "puripuly_heart.ui" not in production


def test_shared_oauth_runtime_has_no_pkce_owner_or_client_lifecycle() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/puripuly_heart/core/runtime/oauth.py").read_text(encoding="utf-8")
    assert "run_openrouter_pkce_flow" not in source
    assert "_openrouter_pkce_client" not in source
    assert "reopen_openrouter_pkce_authorization_url" not in source


def test_pkce_handoff_uses_its_exact_runtime_apply_port() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/puripuly_heart/app/services/openrouter_pkce_handoff.py").read_text(
        encoding="utf-8"
    )
    assert "OpenRouterPkceRuntimeApplyPort" in source
    assert "runtime_apply: RuntimeApplyPort" not in source

    adapter = (root / "src/puripuly_heart/app/adapters/openrouter_pkce_production.py").read_text(
        encoding="utf-8"
    )
    assert ") -> OpenRouterPkceRuntimeApplyResult:" in adapter
