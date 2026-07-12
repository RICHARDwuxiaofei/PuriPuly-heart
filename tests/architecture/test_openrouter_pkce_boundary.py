from __future__ import annotations

import ast
from pathlib import Path


def test_ui_and_controller_do_not_own_openrouter_pkce_resources() -> None:
    root = Path(__file__).resolve().parents[2]
    forbidden = {
        "OpenRouterPKCEClient",
        "_openrouter_pkce_client",
        "run_desktop_flow",
        "_create_callback_listener",
        "callback_origin",
    }
    for relative in (
        "src/puripuly_heart/ui/app.py",
        "src/puripuly_heart/ui/controller.py",
        "src/puripuly_heart/ui/views/settings.py",
    ):
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


def test_pkce_controller_tests_have_no_unconditional_early_return() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "tests/ui/test_controller_branch_paths.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        "test_connect_openrouter_via_pkce_stores_key_sets_alias_and_marks_verified",
        "test_connect_openrouter_via_pkce_rejects_unverified_exchanged_key",
        "test_connect_openrouter_via_pkce_rebuilds_llm_when_signature_is_unchanged",
        "test_connect_openrouter_via_pkce_leaves_settings_unchanged_on_failure",
        "test_connect_openrouter_via_pkce_reopens_letter_context_on_letter_failure",
        "test_connect_openrouter_via_pkce_returns_degraded_on_runtime_apply_failure",
        "test_connect_openrouter_via_pkce_restores_secret_on_settings_commit_failure",
    }
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            found.add(node.name)
            assert not any(isinstance(statement, ast.Return) for statement in node.body), node.name
    assert found == names
