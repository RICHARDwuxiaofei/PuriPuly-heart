from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from puripuly_heart.app import wiring
from puripuly_heart.config import llm_profiles
from puripuly_heart.config import prompts as prompts_module
from puripuly_heart.config.prompts import (
    TRANSLATION_PROMPT_NAME,
    load_prompt,
    load_prompt_for_provider,
)
from puripuly_heart.config.settings import AppSettings, OpenRouterCredentialSource, to_dict
from puripuly_heart.core import (
    managed_identity,
    openrouter_credentials,
)
from puripuly_heart.core import managed_openrouter_broker_client as broker_client
from puripuly_heart.core.overlay import manifest as overlay_manifest_module
from puripuly_heart.core.overlay.manifest import OVERLAY_CONTRACT_VERSION
from puripuly_heart.core.overlay.protocol import (
    OverlayPresentationBlock,
    OverlayPresentationCalibration,
)
from puripuly_heart.core.storage.secrets import InMemorySecretStore, KeyringSecretStore
from tests.config.settings_migration_fixtures import (
    maximal_v24_settings_fixture,
    serialized_field_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PACKAGE_ROOT = REPO_ROOT / "src" / "puripuly_heart"
PACKAGE_NAME = "puripuly_heart"
SNAPSHOT_PATH = Path(__file__).with_name("public_compatibility_surfaces_snapshot.json")
INVENTORY_PATH = Path(__file__).with_name("compatibility_surface_inventory.json")

REQUIRED_SURFACES = (
    "secret_store",
    "broker_v1",
    "overlay",
    "prompts",
    "provider_aliases",
    "guard_coverage",
    "blockers",
)
REQUIRED_INVENTORY_SURFACES = (
    "public_import_facades",
    "settings_runtime_compatibility",
    "persisted_operational_state",
    "secret_store",
    "provider_aliases",
    "provider_runtime_public_config",
    "prompt_fallback",
    "broker_v1",
    "overlay_protocol_startup_snapshot",
    "i18n_key_parity",
    "installer_identity",
    "rust_overlay_startup",
)
REQUIRED_INVENTORY_SOURCE_RULES = {
    "__all__",
    "lazy __getattr__",
    "documented entry point",
    "existing public test or packaging import",
    "approved facade",
}
DOCUMENTED_ENTRY_POINT_OR_TEST_IMPORT_MODULES = frozenset(
    {
        "puripuly_heart.app.wiring",
        "puripuly_heart.config.llm_profiles",
        "puripuly_heart.config.prompts",
        "puripuly_heart.config.settings",
        "puripuly_heart.core.managed_identity",
        "puripuly_heart.core.managed_openrouter_broker_client",
        "puripuly_heart.core.managed_openrouter_release",
        "puripuly_heart.core.openrouter_credentials",
        "puripuly_heart.core.overlay.manifest",
        "puripuly_heart.core.overlay.protocol",
        "puripuly_heart.core.storage.secrets",
        "puripuly_heart.domain.events",
        "puripuly_heart.domain.models",
        "puripuly_heart.main",
        "puripuly_heart.providers.llm.deepseek",
        "puripuly_heart.providers.llm.gemini",
        "puripuly_heart.providers.llm.local_openai",
        "puripuly_heart.providers.llm.openrouter",
        "puripuly_heart.providers.llm.qwen",
        "puripuly_heart.providers.llm.qwen_async",
        "puripuly_heart.providers.stt.deepgram",
        "puripuly_heart.providers.stt.local_qwen_sherpa",
        "puripuly_heart.providers.stt.qwen_asr",
        "puripuly_heart.providers.stt.soniox",
        "puripuly_heart.ui.event_bridge",
    }
)
REQUIRED_SECRET_KEYS = (
    "google_api_key",
    "openrouter_api_key",
    "openrouter_managed_api_key",
    "openrouter_managed_user_id",
    "openrouter_managed_user_installation_id",
    "deepseek_api_key",
    "deepgram_api_key",
    "soniox_api_key",
    "alibaba_api_key_beijing",
    "alibaba_api_key_singapore",
    "alibaba_api_key",
    "local_llm_api_key",
    "managed_device_private_key",
    "managed_device_public_key",
    "managed_identity_binding",
)
WIRING_SECRET_KEYS = (
    "google_api_key",
    "deepseek_api_key",
    "deepgram_api_key",
    "soniox_api_key",
    "alibaba_api_key_beijing",
    "alibaba_api_key_singapore",
    "alibaba_api_key",
    "local_llm_api_key",
)
EXPECTED_SECRET_ENV_LOOKUP_PATHS = (
    {
        "owner": "gemini_llm",
        "lookup": "require_secret",
        "key": "google_api_key",
        "env_vars": ["GOOGLE_API_KEY"],
        "legacy_keys": [],
    },
    {
        "owner": "openrouter_byok_llm",
        "lookup": "openrouter_byok",
        "key": "openrouter_api_key",
        "env_vars": ["OPENROUTER_API_KEY"],
        "legacy_keys": [],
    },
    {
        "owner": "deepseek_llm",
        "lookup": "require_secret",
        "key": "deepseek_api_key",
        "env_vars": ["DEEPSEEK_API_KEY"],
        "legacy_keys": [],
    },
    {
        "owner": "qwen_beijing_llm_stt_peer_stt",
        "lookup": "require_secret_any",
        "key": "alibaba_api_key_beijing",
        "env_vars": ["ALIBABA_API_KEY_BEIJING", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"],
        "legacy_keys": ["alibaba_api_key"],
    },
    {
        "owner": "qwen_singapore_llm_stt_peer_stt",
        "lookup": "require_secret_any",
        "key": "alibaba_api_key_singapore",
        "env_vars": ["ALIBABA_API_KEY_SINGAPORE", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"],
        "legacy_keys": ["alibaba_api_key"],
    },
    {
        "owner": "deepgram_stt_peer_stt",
        "lookup": "require_secret",
        "key": "deepgram_api_key",
        "env_vars": ["DEEPGRAM_API_KEY"],
        "legacy_keys": [],
    },
    {
        "owner": "soniox_stt_peer_stt",
        "lookup": "require_secret",
        "key": "soniox_api_key",
        "env_vars": ["SONIOX_API_KEY"],
        "legacy_keys": [],
    },
    {
        "owner": "local_llm_optional",
        "lookup": "optional_secret_store_only",
        "key": "local_llm_api_key",
        "env_vars": [],
        "legacy_keys": [],
        "ignored_env_vars": ["LOCAL_LLM_API_KEY"],
    },
)
EXPECTED_PROMPT_FALLBACK_ORDER = (
    "{name}.md",
    "{name}.txt",
    "default.md",
    "default.txt",
)


@pytest.fixture(autouse=True)
def reset_prompt_cache() -> Iterator[None]:
    prompts_module._reset_prompt_cache_for_tests()
    yield
    prompts_module._reset_prompt_cache_for_tests()


def _load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _load_inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _test_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _module_name_for_source_path(path: Path) -> str:
    relative = path.relative_to(SOURCE_PACKAGE_ROOT).with_suffix("")
    parts = (PACKAGE_NAME, *relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_non_private_source_module(path: Path) -> bool:
    relative = path.relative_to(SOURCE_PACKAGE_ROOT)
    parts = relative.with_suffix("").parts
    return not any(part.startswith("_") and part != "__init__" for part in parts)


def _module_public_export_signals(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    signals: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            try:
                exported_names = ast.literal_eval(node.value)
            except (SyntaxError, ValueError):
                signals.add("__all__")
                continue
            if exported_names:
                signals.add("__all__")
        elif (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "__getattr__"
        ):
            signals.add("lazy __getattr__")
    return frozenset(signals)


def _relevant_non_private_inventory_modules() -> dict[str, frozenset[str]]:
    relevant: dict[str, frozenset[str]] = {}
    for path in SOURCE_PACKAGE_ROOT.rglob("*.py"):
        if not _is_non_private_source_module(path):
            continue

        module_name = _module_name_for_source_path(path)
        signals = set(_module_public_export_signals(path))
        if module_name in DOCUMENTED_ENTRY_POINT_OR_TEST_IMPORT_MODULES:
            signals.add("documented/test import")
        if signals:
            relevant[module_name] = frozenset(signals)
    return relevant


def _inventory_accounts_for_module(inventory: dict[str, Any], module_name: str) -> bool:
    public_modules = {entry["module"] for entry in inventory["public_imports"]}
    if module_name in public_modules:
        return True

    for classification in inventory["non_public_module_classifications"]:
        if module_name in classification.get("modules", []):
            return True
        if any(
            module_name == prefix or module_name.startswith(f"{prefix}.")
            for prefix in classification.get("module_prefixes", [])
        ):
            return True
    return False


def _leaf_values(value: object) -> Iterator[object]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaf_values(child)
        return
    if isinstance(value, list):
        for child in value:
            yield from _leaf_values(child)
        return
    yield value


def _assert_source_ref_exists(ref: str) -> None:
    path_text = ref.split("::", maxsplit=1)[0]
    assert path_text, ref
    assert (REPO_ROOT / path_text).exists(), ref


def _assert_inventory_export_resolves(module_name: str, export: dict[str, str]) -> None:
    export_name = export["name"]
    if export["kind"] == "submodule":
        importlib.import_module(f"{module_name}.{export_name}")
        return

    module = importlib.import_module(module_name)
    assert hasattr(module, export_name), f"{module_name}.{export_name}"


def _clear_entry_env(monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any]) -> None:
    for env_var in [*entry["env_vars"], *entry.get("ignored_env_vars", [])]:
        monkeypatch.delenv(env_var, raising=False)


def _assert_required_secret_env_lookup(
    monkeypatch: pytest.MonkeyPatch,
    entry: dict[str, Any],
) -> None:
    key = entry["key"]
    env_vars = entry["env_vars"]
    assert len(env_vars) == 1
    env_var = env_vars[0]
    _clear_entry_env(monkeypatch, entry)
    monkeypatch.setenv(env_var, "fake-env-secret")

    assert wiring.require_secret(InMemorySecretStore(), key=key, env_var=env_var) == (
        "fake-env-secret"
    )

    store = InMemorySecretStore()
    store.set(key, "fake-store-secret")
    assert wiring.require_secret(store, key=key, env_var=env_var) == "fake-store-secret"


def _assert_required_any_secret_env_lookup(
    monkeypatch: pytest.MonkeyPatch,
    entry: dict[str, Any],
) -> None:
    key = entry["key"]
    env_vars = tuple(entry["env_vars"])
    legacy_keys = tuple(entry["legacy_keys"])

    store = InMemorySecretStore()
    store.set(key, "fake-store-secret")
    for legacy_key in legacy_keys:
        store.set(legacy_key, "fake-legacy-secret")
    for env_var in env_vars:
        monkeypatch.setenv(env_var, f"fake-{env_var}")
    assert (
        wiring.require_secret_any(
            store,
            key=key,
            env_vars=env_vars,
            legacy_keys=legacy_keys,
        )
        == "fake-store-secret"
    )

    legacy_store = InMemorySecretStore()
    for legacy_key in legacy_keys:
        legacy_store.set(legacy_key, "fake-legacy-secret")
    assert (
        wiring.require_secret_any(
            legacy_store,
            key=key,
            env_vars=env_vars,
            legacy_keys=legacy_keys,
        )
        == "fake-legacy-secret"
    )
    assert legacy_store.get(key) == "fake-legacy-secret"

    for selected_env_var in env_vars:
        _clear_entry_env(monkeypatch, entry)
        monkeypatch.setenv(selected_env_var, f"fake-{selected_env_var}")
        assert (
            wiring.require_secret_any(
                InMemorySecretStore(),
                key=key,
                env_vars=env_vars,
                legacy_keys=legacy_keys,
            )
            == f"fake-{selected_env_var}"
        )


def _assert_openrouter_byok_env_lookup(
    monkeypatch: pytest.MonkeyPatch,
    entry: dict[str, Any],
) -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    env_var = entry["env_vars"][0]
    _clear_entry_env(monkeypatch, entry)
    monkeypatch.setenv(env_var, "fake-env-openrouter")

    env_resolution = openrouter_credentials.resolve_openrouter_credentials(
        settings,
        secrets=InMemorySecretStore(),
    )
    assert env_resolution.api_key == "fake-env-openrouter"

    store = InMemorySecretStore()
    store.set(entry["key"], "fake-store-openrouter")
    store_resolution = openrouter_credentials.resolve_openrouter_credentials(
        settings, secrets=store
    )
    assert store_resolution.api_key == "fake-store-openrouter"


def _assert_local_llm_optional_secret_store_only(entry: dict[str, Any]) -> None:
    source = "\n".join(
        (
            inspect.getsource(wiring.create_llm_provider),
            inspect.getsource(wiring._base_llm_provider_from_resolved_config),
        )
    )

    assert entry["env_vars"] == []
    assert entry["ignored_env_vars"] == ["LOCAL_LLM_API_KEY"]
    assert 'secrets.get("local_llm_api_key")' in source
    assert "LOCAL_LLM_API_KEY" not in source


def _assert_qwen_resolved_credential_helper_preserves_legacy_fallbacks() -> None:
    helper_source = inspect.getsource(wiring._qwen_api_key_for_resolved_credential)
    base_llm_source = inspect.getsource(wiring._base_llm_provider_from_resolved_config)
    stt_source = inspect.getsource(wiring.create_stt_backend_from_resolved_config)
    self_stt_source = inspect.getsource(wiring.create_stt_backend)
    peer_stt_source = inspect.getsource(wiring.create_peer_stt_backend)
    peer_resolved_source = inspect.getsource(wiring.create_peer_stt_backend_from_resolved_config)

    assert 'key="alibaba_api_key_beijing"' in helper_source
    assert 'key="alibaba_api_key_singapore"' in helper_source
    assert '"ALIBABA_API_KEY_BEIJING"' in helper_source
    assert '"ALIBABA_API_KEY_SINGAPORE"' in helper_source
    assert '"ALIBABA_API_KEY"' in helper_source
    assert '"DASHSCOPE_API_KEY"' in helper_source
    assert helper_source.count('legacy_keys=("alibaba_api_key",)') == 2
    assert "_qwen_api_key_for_resolved_credential(config.credential" in base_llm_source
    assert "_qwen_api_key_for_resolved_credential(config.credential" in stt_source
    assert "create_stt_backend_from_resolved_config(" in self_stt_source
    assert "create_peer_stt_backend_from_resolved_config(" in peer_stt_source
    assert "create_stt_backend_from_resolved_config(" in peer_resolved_source


def test_public_compatibility_snapshot_declares_all_required_surfaces() -> None:
    snapshot = _load_snapshot()

    assert tuple(snapshot) == REQUIRED_SURFACES
    assert snapshot["blockers"] == []


def test_gate_zero_compatibility_inventory_declares_required_surfaces() -> None:
    inventory = _load_inventory()

    assert inventory["bundle"]["ref"] == "vnext-internal-cutover"
    assert inventory["bundle"]["sha256"] == (
        "033912b4bf580cd5d488066dd9139c3ff0896d962df94e5de405bb3c694dd1b6"
    )
    assert inventory["source_spec"]["sha256"] == (
        "42e4a691eacdbbc495eb5ede54c0c3511b1c0c78b67159ead94d2bad35c0c1f9"
    )
    assert tuple(inventory["compatibility_surfaces"]) == REQUIRED_INVENTORY_SURFACES
    assert inventory["ambiguous_public_private_surfaces"] == []

    for surface_name, surface in inventory["compatibility_surfaces"].items():
        assert surface["classification"] == "public_compatibility_preserve", surface_name
        assert surface["source_of_truth_refs"], surface_name
        assert surface["fixture_or_guard_refs"], surface_name
        for ref in [*surface["source_of_truth_refs"], *surface["fixture_or_guard_refs"]]:
            _assert_source_ref_exists(ref)


def test_public_import_inventory_smoke_imports_every_exported_name() -> None:
    public_imports = _load_inventory()["public_imports"]

    assert public_imports
    for entry in public_imports:
        module_name = entry["module"]
        source_rule = entry["source_rule"]
        exports = entry["exports"]

        assert source_rule in REQUIRED_INVENTORY_SOURCE_RULES, module_name
        assert exports, module_name
        for ref in entry["source_of_truth_refs"]:
            _assert_source_ref_exists(ref)

        module = importlib.import_module(module_name)
        if entry.get("runtime_all_matches_exports"):
            assert tuple(module.__all__) == tuple(export["name"] for export in exports)
        for export in exports:
            _assert_inventory_export_resolves(module_name, export)


def test_public_facade_inventory_classifies_thin_delegate_targets() -> None:
    inventory = _load_inventory()
    public_imports = inventory["public_imports"]
    facade_entries = [entry for entry in public_imports if entry["facade_contract"]]

    assert facade_entries
    for entry in facade_entries:
        contract = entry["facade_contract"]
        assert contract["preserve_import_path"] is True, entry["module"]
        assert contract["target_role"] in {
            "thin_delegate",
            "thin_reexport",
            "lazy_reexport",
            "compatibility_boundary_owner",
        }, entry["module"]
        assert contract["implementation_owner"] in {
            "canonical_vnext_owner",
            "compatibility_boundary",
            "adapter_owner_until_split",
            "public_contract_owner",
        }, entry["module"]

    for classification in inventory["non_public_module_classifications"]:
        assert classification["classification"] != "ambiguous", classification
        assert classification["rationale"], classification


def test_relevant_non_private_modules_are_inventoried_or_classified() -> None:
    inventory = _load_inventory()
    relevant_modules = _relevant_non_private_inventory_modules()

    missing = {
        module_name: sorted(signals)
        for module_name, signals in sorted(relevant_modules.items())
        if not _inventory_accounts_for_module(inventory, module_name)
    }

    assert missing == {}


def test_guard_coverage_references_existing_tests_for_every_surface() -> None:
    coverage = _load_snapshot()["guard_coverage"]
    expected_surfaces = {
        "secret_store",
        "broker_v1",
        "overlay",
        "prompt_loader",
        "provider_aliases",
        "i18n_parity",
        "packaging",
    }

    assert set(coverage) == expected_surfaces
    for surface, refs in coverage.items():
        assert refs, surface
        for ref in refs:
            file_name, separator, test_name = ref.partition("::")
            assert separator == "::", ref
            test_path = REPO_ROOT / file_name
            assert test_path.is_file(), ref
            assert test_name in _test_function_names(test_path), ref


def test_secret_store_key_registry_snapshot_matches_current_public_keys() -> None:
    snapshot = _load_snapshot()["secret_store"]
    registry_keys = tuple(snapshot["registry_keys"])

    assert registry_keys == REQUIRED_SECRET_KEYS
    assert KeyringSecretStore().service_name == snapshot["keyring_service_name"]
    assert openrouter_credentials.OPENROUTER_BYOK_API_KEY_SECRET == "openrouter_api_key"
    assert openrouter_credentials.OPENROUTER_MANAGED_API_KEY_SECRET == (
        "openrouter_managed_api_key"
    )
    assert openrouter_credentials.OPENROUTER_MANAGED_USER_ID_SECRET == (
        "openrouter_managed_user_id"
    )
    assert openrouter_credentials.OPENROUTER_MANAGED_USER_INSTALLATION_ID_SECRET == (
        "openrouter_managed_user_installation_id"
    )
    assert managed_identity.MANAGED_DEVICE_PRIVATE_KEY_SECRET == "managed_device_private_key"
    assert managed_identity.MANAGED_DEVICE_PUBLIC_KEY_SECRET == "managed_device_public_key"
    assert managed_identity.MANAGED_IDENTITY_BINDING_SECRET == "managed_identity_binding"

    wiring_source = inspect.getsource(wiring)
    for key in WIRING_SECRET_KEYS:
        assert f'"{key}"' in wiring_source
    _assert_qwen_resolved_credential_helper_preserves_legacy_fallbacks()


def test_secret_store_env_lookup_snapshot_matches_current_fallback_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = tuple(_load_snapshot()["secret_store"]["env_lookup_paths"])

    assert entries == EXPECTED_SECRET_ENV_LOOKUP_PATHS
    for entry in entries:
        _clear_entry_env(monkeypatch, entry)
        lookup = entry["lookup"]
        if lookup == "require_secret":
            _assert_required_secret_env_lookup(monkeypatch, entry)
        elif lookup == "require_secret_any":
            _assert_required_any_secret_env_lookup(monkeypatch, entry)
        elif lookup == "openrouter_byok":
            _assert_openrouter_byok_env_lookup(monkeypatch, entry)
        elif lookup == "optional_secret_store_only":
            _assert_local_llm_optional_secret_store_only(entry)
        else:  # pragma: no cover - snapshot guard should fail before this branch matters.
            raise AssertionError(f"unknown SecretStore lookup snapshot type: {lookup!r}")


@pytest.mark.parametrize(
    "current_key",
    ["alibaba_api_key_beijing", "alibaba_api_key_singapore"],
)
def test_secret_store_accepts_legacy_alibaba_key_and_backfills_current_key(
    current_key: str,
) -> None:
    store = InMemorySecretStore()
    store.set("alibaba_api_key", "fake-legacy-alibaba-key")

    value = wiring.require_secret_any(
        store,
        key=current_key,
        env_vars=(),
        legacy_keys=("alibaba_api_key",),
    )

    assert value == "fake-legacy-alibaba-key"
    assert store.get(current_key) == "fake-legacy-alibaba-key"


def test_settings_serialization_excludes_secret_store_registry_keys() -> None:
    registry_keys = set(_load_snapshot()["secret_store"]["registry_keys"])
    serialized_settings = (
        to_dict(AppSettings()),
        maximal_v24_settings_fixture(),
    )

    for data in serialized_settings:
        paths = set(serialized_field_paths(data))
        forbidden_paths = sorted(
            path
            for path in paths
            if not path.startswith("api_key_verified.")
            and any(part in registry_keys for part in path.split("."))
        )
        string_values = {value for value in _leaf_values(data) if isinstance(value, str)}

        assert forbidden_paths == []
        assert registry_keys.isdisjoint(string_values)


def test_broker_v1_snapshot_matches_client_paths_and_public_error_vocabulary() -> None:
    snapshot = _load_snapshot()["broker_v1"]
    client_source = inspect.getsource(broker_client.HttpManagedOpenRouterBrokerClient)
    path_literals = tuple(sorted(set(re.findall(r'path="([^"]+)"', client_source))))

    assert path_literals == tuple(snapshot["paths"])
    assert all(path.startswith("/v1/") for path in path_literals)
    assert "/v2/" not in inspect.getsource(broker_client)
    assert tuple(sorted(broker_client.PUBLIC_ERROR_CODES)) == tuple(snapshot["public_error_codes"])
    assert tuple(sorted(broker_client.PUBLIC_ERROR_CLASSES)) == tuple(
        snapshot["public_error_classes"]
    )


def test_overlay_contract_snapshot_matches_manifest_and_protocol_wire_shape() -> None:
    snapshot = _load_snapshot()["overlay"]
    block = OverlayPresentationBlock(
        id="self:1",
        occupant_key="self:1",
        appearance_seq=1,
        channel="self",
        block_variant="finalized",
        primary_text="hello",
        secondary_text="안녕",
        secondary_enabled=True,
        primary_language="en",
        secondary_language="ko",
        update_id="update-1",
        origin_wall_clock_ms=1712345678901,
        session_scope="session:self",
        source_text_hash="abc123",
        source_text_len=5,
        logical_turn_key="self:1",
    )

    assert OVERLAY_CONTRACT_VERSION == snapshot["contract_version"]
    assert tuple(sorted(overlay_manifest_module._MANIFEST_FIELDS)) == tuple(
        snapshot["manifest_fields"]
    )
    assert tuple(OverlayPresentationCalibration().to_dict()) == tuple(
        snapshot["calibration_fields"]
    )
    assert tuple(block.to_dict()) == tuple(snapshot["presentation_block_fields"])
    assert tuple(snapshot["channels"]) == ("self", "peer")
    assert tuple(snapshot["block_variants"]) == ("active_self", "active_peer", "finalized")


def test_prompt_loader_snapshot_freezes_fallback_order_and_translation_prompt_requirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _load_snapshot()["prompts"]
    prompt_name = "surface"
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    files = {
        f"{prompt_name}.md": "name-md",
        f"{prompt_name}.txt": "name-txt",
        "default.md": "default-md",
        "default.txt": "default-txt",
    }
    for file_name, content in files.items():
        (prompts_dir / file_name).write_text(content, encoding="utf-8")
    monkeypatch.setenv("PURIPULY_HEART_PROMPTS_DIR", str(prompts_dir))

    assert snapshot["translation_prompt_name"] == TRANSLATION_PROMPT_NAME
    assert tuple(snapshot["load_prompt_fallback_order"]) == EXPECTED_PROMPT_FALLBACK_ORDER
    assert tuple(snapshot["llm_provider_prompt_keys"]) == tuple(
        sorted(prompts_module._LLM_PROVIDER_PROMPT_KEYS)
    )
    assert load_prompt(prompt_name) == "name-md"
    (prompts_dir / f"{prompt_name}.md").unlink()
    assert load_prompt(prompt_name) == "name-txt"
    (prompts_dir / f"{prompt_name}.txt").unlink()
    assert load_prompt(prompt_name) == "default-md"
    (prompts_dir / "default.md").unlink()
    assert load_prompt(prompt_name) == "default-txt"
    (prompts_dir / "default.txt").unlink()
    assert load_prompt(prompt_name) == ""

    with pytest.raises(FileNotFoundError):
        load_prompt_for_provider("gemini")


def test_provider_alias_snapshot_matches_current_aliases_and_legacy_acceptance() -> None:
    snapshot = _load_snapshot()["provider_aliases"]

    assert tuple(snapshot["openrouter_main_selection_aliases"]) == (
        llm_profiles.OPENROUTER_MAIN_SELECTION_ALIASES
    )
    assert tuple(snapshot["openrouter_fallback_selection_aliases"]) == (
        llm_profiles.OPENROUTER_FALLBACK_SELECTION_ALIASES
    )
    assert tuple(snapshot["legacy_selection_aliases"]) == tuple(
        sorted(llm_profiles.LEGACY_PROFILE_BY_ALIAS)
    )
    assert snapshot["legacy_fallback_aliases"] == llm_profiles.LEGACY_FALLBACK_ALIAS_TO_ALIAS

    for alias in snapshot["legacy_selection_aliases"]:
        assert llm_profiles.get_openrouter_llm_profile(alias) is not None
    for legacy_alias, canonical_alias in snapshot["legacy_fallback_aliases"].items():
        assert llm_profiles.normalize_openrouter_fallback_selection_alias(legacy_alias) == (
            canonical_alias
        )
