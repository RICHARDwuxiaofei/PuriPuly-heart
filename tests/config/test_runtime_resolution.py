from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

MODULE_NAME = "puripuly_heart.config.runtime_resolution"

ALLOWED_INTERNAL_IMPORTS = frozenset(
    {
        "puripuly_heart.config.llm_profiles",
        "puripuly_heart.config.resolved",
    }
)
FORBIDDEN_INTERNAL_IMPORT_PREFIXES = (
    "puripuly_heart.app",
    "puripuly_heart.config.settings",
    "puripuly_heart.core.managed_openrouter_broker_client",
    "puripuly_heart.core.storage",
    "puripuly_heart.providers",
    "puripuly_heart.ui",
)
FORBIDDEN_EXTERNAL_IMPORT_ROOTS = frozenset({"flet", "httpx", "keyring", "requests"})
FORBIDDEN_FILE_IO_CALL_NAMES = frozenset({"open"})
FORBIDDEN_FILE_IO_ATTR_CALLS = frozenset(
    {"mkdir", "open", "read_bytes", "read_text", "unlink", "write_bytes", "write_text"}
)


def _runtime_resolution_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


def _resolved_module() -> ModuleType:
    return importlib.import_module("puripuly_heart.config.resolved")


def _profiles_module() -> ModuleType:
    return importlib.import_module("puripuly_heart.config.llm_profiles")


def _load_boundary_guard() -> ModuleType:
    guard_path = (
        Path(__file__).resolve().parents[1] / "architecture" / ("test_dependency_boundaries.py")
    )
    spec = importlib.util.spec_from_file_location("_runtime_resolution_boundary_guard", guard_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _imported_modules_from_source(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _assert_no_file_io_calls(source_path: Path) -> None:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_FILE_IO_CALL_NAMES
        if isinstance(node.func, ast.Attribute):
            assert node.func.attr not in FORBIDDEN_FILE_IO_ATTR_CALLS


def _runtime_input(
    runtime_resolution: ModuleType,
    *,
    model: str,
    connection: str,
    openrouter: Any | None = None,
    direct: Any | None = None,
    concurrency_limit: int = 5,
) -> Any:
    return runtime_resolution.RuntimeResolutionInput(
        translation=runtime_resolution.TranslationRuntimeIntent(
            model=model,
            connection=connection,
            concurrency_limit=concurrency_limit,
        ),
        openrouter=openrouter or runtime_resolution.OpenRouterRuntimeIntent(),
        direct=direct or runtime_resolution.DirectProviderRuntimeIntent(),
    )


def _credential_assertion(resolved: ModuleType, source: str, reference: str | None) -> Any:
    return resolved.ResolvedCredentialRequirement(
        source=source,
        required=source != resolved.CREDENTIAL_SOURCE_NONE,
        reference=reference,
    )


def test_runtime_resolution_module_is_import_safe_and_dependency_light() -> None:
    runtime_resolution = _runtime_resolution_module()
    source_path = Path(runtime_resolution.__file__ or "")

    assert source_path.name == "runtime_resolution.py"
    imported_modules = _imported_modules_from_source(source_path)
    for imported_module in imported_modules:
        if imported_module.startswith("puripuly_heart."):
            assert imported_module in ALLOWED_INTERNAL_IMPORTS
        assert not imported_module.startswith(FORBIDDEN_INTERNAL_IMPORT_PREFIXES)
        assert imported_module.split(".", 1)[0] not in FORBIDDEN_EXTERNAL_IMPORT_ROOTS
    _assert_no_file_io_calls(source_path)


def test_runtime_resolution_layer_is_covered_by_dependency_boundary_guard() -> None:
    runtime_resolution = _runtime_resolution_module()
    guard = _load_boundary_guard()

    assert guard._layer_for_module(runtime_resolution.__name__) == guard.RUNTIME_RESOLUTION
    rule = guard._rule_for_layer(guard.RUNTIME_RESOLUTION)
    assert runtime_resolution.__name__ in rule.prefixes
    assert rule.rule_id == "runtime-resolution-stays-pure"


def test_canonical_runtime_intent_contracts_are_frozen_and_slotted() -> None:
    runtime_resolution = _runtime_resolution_module()

    for class_name in (
        "DirectProviderRuntimeIntent",
        "OpenRouterRuntimeIntent",
        "RuntimeResolutionInput",
        "TranslationRuntimeIntent",
    ):
        dto_class = getattr(runtime_resolution, class_name)
        assert is_dataclass(dto_class)
        assert dto_class.__dataclass_params__.frozen is True
        assert hasattr(dto_class, "__slots__")
        assert "__dict__" not in dto_class.__slots__

    intent = runtime_resolution.TranslationRuntimeIntent(
        model=runtime_resolution.TRANSLATION_MODEL_GEMMA4,
        connection=runtime_resolution.TRANSLATION_CONNECTION_MANAGED,
    )
    with pytest.raises(FrozenInstanceError):
        intent.model = runtime_resolution.TRANSLATION_MODEL_LOCAL_LLM


@pytest.mark.parametrize(
    (
        "model",
        "connection",
        "openrouter_source",
        "expected_provider",
        "expected_model",
        "expected_credential_source",
        "expected_credential_reference",
        "expected_region",
        "expected_provider_routing",
    ),
    [
        (
            "gemma4",
            "managed",
            "managed",
            "openrouter",
            "google/gemma-4-26b-a4b-it",
            "managed",
            "openrouter:managed",
            None,
            "default",
        ),
        (
            "gemma4",
            "openrouter",
            "byok",
            "openrouter",
            "google/gemma-4-26b-a4b-it",
            "secret_store",
            "openrouter:byok",
            None,
            "default",
        ),
        (
            "gemma4",
            "openrouter",
            "none",
            "openrouter",
            "google/gemma-4-26b-a4b-it",
            "none",
            None,
            None,
            "default",
        ),
        (
            "deepseek_v4_flash",
            "managed",
            "managed",
            "openrouter",
            "deepseek/deepseek-v4-flash",
            "managed",
            "openrouter:managed",
            None,
            "default",
        ),
        (
            "deepseek_v4_flash",
            "managed_china",
            "managed",
            "openrouter",
            "deepseek/deepseek-v4-flash",
            "managed",
            "openrouter:managed",
            None,
            "deepseek_only",
        ),
        (
            "deepseek_v4_flash",
            "openrouter",
            "byok",
            "openrouter",
            "deepseek/deepseek-v4-flash",
            "secret_store",
            "openrouter:byok",
            None,
            "default",
        ),
        (
            "deepseek_v4_flash",
            "official_byok",
            "byok",
            "deepseek",
            "deepseek-v4-flash",
            "secret_store",
            "deepseek:byok",
            None,
            None,
        ),
        (
            "deepseek_v4_pro",
            "official_byok",
            "byok",
            "deepseek",
            "deepseek-v4-pro",
            "secret_store",
            "deepseek:byok",
            None,
            None,
        ),
        (
            "gemini3_flash",
            "official_byok",
            "byok",
            "gemini",
            "gemini-3-flash-preview",
            "secret_store",
            "gemini:byok",
            None,
            None,
        ),
        (
            "gemini31_flash_lite",
            "official_byok",
            "byok",
            "gemini",
            "gemini-3.1-flash-lite",
            "secret_store",
            "gemini:byok",
            None,
            None,
        ),
        (
            "qwen35_plus",
            "official_byok",
            "byok",
            "qwen",
            "qwen3.5-plus",
            "secret_store",
            "qwen:beijing",
            "beijing",
            None,
        ),
        (
            "local_llm",
            "ollama",
            "byok",
            "local_llm",
            "llama3.1:8b",
            "none",
            None,
            None,
            None,
        ),
    ],
)
def test_translation_model_connection_matrix_resolves_llm_config(
    model: str,
    connection: str,
    openrouter_source: str,
    expected_provider: str,
    expected_model: str,
    expected_credential_source: str,
    expected_credential_reference: str | None,
    expected_region: str | None,
    expected_provider_routing: str | None,
) -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    openrouter_intent = runtime_resolution.OpenRouterRuntimeIntent(
        selected_source=openrouter_source,
        fallback_selection_alias=runtime_resolution.OPENROUTER_FALLBACK_SELECTION_ALIAS_NONE,
    )

    config = runtime_resolution.resolve_llm_config(
        _runtime_input(
            runtime_resolution,
            model=model,
            connection=connection,
            openrouter=openrouter_intent,
        )
    )

    assert isinstance(config, resolved.ResolvedLLMConfig)
    assert config.provider == expected_provider
    assert config.model == expected_model
    assert config.credential == _credential_assertion(
        resolved,
        expected_credential_source,
        expected_credential_reference,
    )
    assert config.region == expected_region
    assert config.provider_routing == expected_provider_routing
    assert config.concurrency_limit == 5
    assert config.fallback_provider is None
    assert config.fallback_model is None
    assert config.fallback_credential.source == resolved.CREDENTIAL_SOURCE_NONE


@pytest.mark.parametrize(
    (
        "connection",
        "selection_alias",
        "fallback_alias",
        "expected_fallback_source",
        "expected_fallback_reference",
        "expected_fallback_model",
    ),
    [
        (
            "managed",
            "gemma4_managed",
            "qwen35_flash",
            "managed",
            "openrouter:managed",
            "qwen/qwen3.5-flash-02-23",
        ),
        (
            "openrouter",
            "gemma4_byok",
            "gemini25_flash_lite",
            "secret_store",
            "openrouter:byok",
            "deepseek/deepseek-v4-flash",
        ),
        (
            "openrouter",
            "openrouter:none:google/gemma-4-26b-a4b-it",
            "deepseek_v4_flash",
            "none",
            None,
            "deepseek/deepseek-v4-flash",
        ),
    ],
)
def test_openrouter_fallback_source_locks_to_main_source_for_aliases(
    connection: str,
    selection_alias: str,
    fallback_alias: str,
    expected_fallback_source: str,
    expected_fallback_reference: str | None,
    expected_fallback_model: str,
) -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        selection_alias=selection_alias,
        fallback_selection_alias=fallback_alias,
    )

    config = runtime_resolution.resolve_llm_config(
        _runtime_input(
            runtime_resolution,
            model=runtime_resolution.TRANSLATION_MODEL_GEMMA4,
            connection=connection,
            openrouter=openrouter_intent,
        )
    )

    assert config.fallback_provider == "openrouter"
    assert config.fallback_model == expected_fallback_model
    assert config.fallback_credential == _credential_assertion(
        resolved,
        expected_fallback_source,
        expected_fallback_reference,
    )


def test_openrouter_no_fallback_selected_has_no_fallback_credential() -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        selection_alias="gemma4_byok",
        fallback_selection_alias="none",
    )

    config = runtime_resolution.resolve_llm_config(
        _runtime_input(
            runtime_resolution,
            model=runtime_resolution.TRANSLATION_MODEL_GEMMA4,
            connection=runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER,
            openrouter=openrouter_intent,
        )
    )

    assert config.fallback_provider is None
    assert config.fallback_model is None
    assert config.fallback_credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_NONE,
        required=False,
        reference=None,
    )
    assert config.fallback_provider_routing is None


def test_openrouter_china_fallback_resolves_deepseek_only_fallback_routing() -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        selection_alias="gemma4_byok",
        fallback_selection_alias="deepseek_v4_flash_china",
    )

    config = runtime_resolution.resolve_llm_config(
        _runtime_input(
            runtime_resolution,
            model=runtime_resolution.TRANSLATION_MODEL_GEMMA4,
            connection=runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER,
            openrouter=openrouter_intent,
        )
    )

    assert config.fallback_provider == "openrouter"
    assert config.fallback_model == "deepseek/deepseek-v4-flash"
    assert config.fallback_credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_SECRET_STORE,
        required=True,
        reference="openrouter:byok",
    )
    assert config.fallback_provider_routing == "deepseek_only"


def test_openrouter_normal_fallback_resolves_default_fallback_routing() -> None:
    runtime_resolution = _runtime_resolution_module()
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        selection_alias="gemma4_byok",
        fallback_selection_alias="qwen35_flash",
    )

    config = runtime_resolution.resolve_llm_config(
        _runtime_input(
            runtime_resolution,
            model=runtime_resolution.TRANSLATION_MODEL_GEMMA4,
            connection=runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER,
            openrouter=openrouter_intent,
        )
    )

    assert config.fallback_provider == "openrouter"
    assert config.fallback_model == "qwen/qwen3.5-flash-02-23"
    assert config.fallback_provider_routing == "default"


def test_openrouter_deepseek_only_primary_suppresses_fallback_fields() -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        selection_alias="deepseek_v4_flash_managed",
        fallback_selection_alias="qwen35_flash",
        provider_routing="deepseek_only",
    )

    config = runtime_resolution.resolve_llm_config(
        _runtime_input(
            runtime_resolution,
            model=runtime_resolution.TRANSLATION_MODEL_DEEPSEEK_V4_FLASH,
            connection=runtime_resolution.TRANSLATION_CONNECTION_MANAGED_CHINA,
            openrouter=openrouter_intent,
        )
    )

    assert config.provider == "openrouter"
    assert config.model == "deepseek/deepseek-v4-flash"
    assert config.provider_routing == "deepseek_only"
    assert config.fallback_provider is None
    assert config.fallback_model is None
    assert config.fallback_credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_NONE,
        required=False,
        reference=None,
    )
    assert config.fallback_provider_routing is None


def test_openrouter_deepseek_byok_deepseek_only_preserves_routing_and_suppresses_fallback() -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        provider_llm="openrouter",
        model="deepseek/deepseek-v4-flash",
        selected_source="byok",
        fallback_selection_alias="qwen35_flash",
        routing_mode="parasail_first",
        provider_routing="deepseek_only",
        broker_base_url="https://broker.fixture.test/v1",
    )
    translation_intent = runtime_resolution.derive_translation_runtime_intent_from_compatibility(
        provider_llm="openrouter",
        openrouter_model=openrouter_intent.model,
        openrouter_selected_source=openrouter_intent.selected_source,
        openrouter_provider_routing=openrouter_intent.provider_routing,
        concurrency_limit=8,
    )

    config = runtime_resolution.resolve_llm_config(
        runtime_resolution.RuntimeResolutionInput(
            translation=translation_intent,
            openrouter=openrouter_intent,
        )
    )

    assert translation_intent.model == runtime_resolution.TRANSLATION_MODEL_DEEPSEEK_V4_FLASH
    assert translation_intent.connection == runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER
    assert config.provider == "openrouter"
    assert config.model == "deepseek/deepseek-v4-flash"
    assert config.credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_SECRET_STORE,
        required=True,
        reference="openrouter:byok",
    )
    assert config.routing_mode == "parasail_first"
    assert config.provider_routing == "deepseek_only"
    assert config.service_endpoint == "https://broker.fixture.test/v1"
    assert config.fallback_provider is None
    assert config.fallback_model is None
    assert config.fallback_credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_NONE,
        required=False,
        reference=None,
    )
    assert config.fallback_provider_routing is None


def test_legacy_current_openrouter_aliases_normalize_to_canonical_intent_and_resolve() -> None:
    runtime_resolution = _runtime_resolution_module()
    profiles = _profiles_module()
    resolved = _resolved_module()

    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        selection_alias=profiles.LEGACY_OPENROUTER_SELECTION_ALIAS_BYOK_GEMMA_4_26B_A4B_IT,
        selected_source=profiles.OPENROUTER_CREDENTIAL_SOURCE_MANAGED,
        fallback_selection_alias=(
            profiles.LEGACY_OPENROUTER_FALLBACK_SELECTION_ALIAS_GEMINI31_FLASH_LITE
        ),
    )

    assert openrouter_intent.model == profiles.OPENROUTER_MODEL_GEMMA_4_26B_A4B_IT
    assert openrouter_intent.selected_source == profiles.OPENROUTER_CREDENTIAL_SOURCE_BYOK
    assert openrouter_intent.selection_alias == profiles.OPENROUTER_SELECTION_ALIAS_GEMMA4_BYOK
    assert (
        openrouter_intent.fallback_selection_alias
        == profiles.OPENROUTER_FALLBACK_SELECTION_ALIAS_DEEPSEEK_V4_FLASH
    )
    config = runtime_resolution.resolve_llm_config(
        _runtime_input(
            runtime_resolution,
            model=runtime_resolution.TRANSLATION_MODEL_GEMMA4,
            connection=runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER,
            openrouter=openrouter_intent,
            concurrency_limit=7,
        )
    )

    assert config.provider == "openrouter"
    assert config.model == profiles.OPENROUTER_MODEL_GEMMA_4_26B_A4B_IT
    assert config.credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_SECRET_STORE,
        required=True,
        reference="openrouter:byok",
    )
    assert config.fallback_model == profiles.OPENROUTER_MODEL_DEEPSEEK_V4_FLASH
    assert config.fallback_credential.source == resolved.CREDENTIAL_SOURCE_SECRET_STORE
    assert config.concurrency_limit == 7


def test_current_and_legacy_setting_value_snapshots_convert_to_canonical_input_and_resolve() -> (
    None
):
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    from tests.config.settings_migration_fixtures import (  # noqa: PLC0415
        legacy_compatibility_settings_fixture,
        maximal_v24_settings_fixture,
    )

    for raw_settings in (
        maximal_v24_settings_fixture(),
        legacy_compatibility_settings_fixture(),
    ):
        raw_openrouter = raw_settings["openrouter"]
        raw_translation = raw_settings["translation"]
        openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
            model=raw_openrouter.get("llm_model"),
            selected_source=(
                raw_openrouter.get("selected_source")
                or raw_openrouter.get("credential_source")
                or raw_openrouter.get("selected_credential_source")
            ),
            selection_alias=raw_openrouter.get("selection_alias"),
            fallback_selection_alias=raw_openrouter.get("fallback_selection_alias"),
            routing_mode=raw_openrouter.get("routing_mode"),
            provider_routing=raw_openrouter.get("provider_routing"),
            broker_base_url=raw_openrouter.get("broker_base_url"),
        )
        translation_intent = runtime_resolution.normalize_translation_runtime_intent(
            model=raw_translation.get("model"),
            connection=raw_translation.get("connection"),
            concurrency_limit=raw_settings["llm"].get("concurrency_limit"),
        )

        config = runtime_resolution.resolve_llm_config(
            runtime_resolution.RuntimeResolutionInput(
                translation=translation_intent,
                openrouter=openrouter_intent,
                direct=runtime_resolution.DirectProviderRuntimeIntent(
                    local_llm_model=raw_settings["local_llm"]["model"],
                    local_llm_base_url=raw_settings["local_llm"]["base_url"],
                    local_llm_extra_body=raw_settings["local_llm"]["extra_body"],
                    qwen_region=raw_settings["qwen"]["region"],
                ),
            )
        )

        assert isinstance(config, resolved.ResolvedLLMConfig)
        assert config.provider in {"deepseek", "gemini", "local_llm", "openrouter", "qwen"}
        assert config.concurrency_limit == raw_settings["llm"]["concurrency_limit"]


def test_missing_translation_openrouter_compatibility_values_derive_exact_runtime_config() -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    raw_settings = {
        "provider": {"llm": "openrouter"},
        "openrouter": {
            "llm_model": "google/gemma-4-26b-a4b-it",
            "selected_source": "managed",
            "selection_alias": "deepseek_v4_flash_byok",
            "fallback_selection_alias": "qwen35_flash",
            "routing_mode": "parasail_first",
            "provider_routing": "default",
            "broker_base_url": "https://broker.fixture.test/v1",
        },
        "gemini": {"llm_model": "gemini-3.1-flash-lite"},
        "qwen": {"llm_model": "qwen3.5-plus", "region": "beijing"},
        "deepseek": {"llm_model": "deepseek-v4-flash"},
        "local_llm": {
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "llama3.1:8b",
            "extra_body": {},
        },
        "llm": {"concurrency_limit": 4},
    }
    raw_openrouter = raw_settings["openrouter"]
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        model=raw_openrouter["llm_model"],
        selected_source=raw_openrouter["selected_source"],
        selection_alias=raw_openrouter["selection_alias"],
        fallback_selection_alias=raw_openrouter["fallback_selection_alias"],
        routing_mode=raw_openrouter["routing_mode"],
        provider_routing=raw_openrouter["provider_routing"],
        broker_base_url=raw_openrouter["broker_base_url"],
    )
    translation_intent = runtime_resolution.derive_translation_runtime_intent_from_compatibility(
        provider_llm=raw_settings["provider"]["llm"],
        openrouter_model=openrouter_intent.model,
        openrouter_selected_source=openrouter_intent.selected_source,
        openrouter_provider_routing=openrouter_intent.provider_routing,
        gemini_model=raw_settings["gemini"]["llm_model"],
        qwen_model=raw_settings["qwen"]["llm_model"],
        deepseek_model=raw_settings["deepseek"]["llm_model"],
        concurrency_limit=raw_settings["llm"]["concurrency_limit"],
    )

    config = runtime_resolution.resolve_llm_config(
        runtime_resolution.RuntimeResolutionInput(
            translation=translation_intent,
            openrouter=openrouter_intent,
            direct=runtime_resolution.DirectProviderRuntimeIntent(
                qwen_region=raw_settings["qwen"]["region"],
                local_llm_model=raw_settings["local_llm"]["model"],
                local_llm_base_url=raw_settings["local_llm"]["base_url"],
                local_llm_extra_body=raw_settings["local_llm"]["extra_body"],
            ),
        )
    )

    assert translation_intent.model == runtime_resolution.TRANSLATION_MODEL_DEEPSEEK_V4_FLASH
    assert translation_intent.connection == runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER
    assert openrouter_intent.model == "deepseek/deepseek-v4-flash"
    assert openrouter_intent.selected_source == "byok"
    assert config.provider == "openrouter"
    assert config.model == "deepseek/deepseek-v4-flash"
    assert config.credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_SECRET_STORE,
        required=True,
        reference="openrouter:byok",
    )
    assert config.routing_mode == "parasail_first"
    assert config.provider_routing == "default"
    assert config.base_url is None
    assert config.service_endpoint == "https://broker.fixture.test/v1"
    assert config.fallback_provider == "openrouter"
    assert config.fallback_model == "qwen/qwen3.5-flash-02-23"
    assert config.fallback_credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_SECRET_STORE,
        required=True,
        reference="openrouter:byok",
    )
    assert config.fallback_provider_routing == "default"
    assert config.concurrency_limit == 4


@pytest.mark.parametrize(
    ("selected_source", "expected_credential_source", "expected_credential_reference"),
    [
        ("byok", "secret_store", "openrouter:byok"),
        ("managed", "managed", "openrouter:managed"),
    ],
)
def test_openrouter_qwen_primary_compatibility_preserves_model_and_source(
    selected_source: str,
    expected_credential_source: str,
    expected_credential_reference: str,
) -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        provider_llm="openrouter",
        model="qwen/qwen3.5-flash-02-23",
        selected_source=selected_source,
        fallback_selection_alias="none",
        routing_mode="parasail_first",
        provider_routing="default",
        broker_base_url="https://broker.fixture.test/v1",
    )
    translation_intent = runtime_resolution.derive_translation_runtime_intent_from_compatibility(
        provider_llm="openrouter",
        openrouter_model=openrouter_intent.model,
        openrouter_selected_source=openrouter_intent.selected_source,
        openrouter_provider_routing=openrouter_intent.provider_routing,
        concurrency_limit=8,
    )

    config = runtime_resolution.resolve_llm_config(
        runtime_resolution.RuntimeResolutionInput(
            translation=translation_intent,
            openrouter=openrouter_intent,
        )
    )

    assert config.provider == "openrouter"
    assert config.model == "qwen/qwen3.5-flash-02-23"
    assert config.credential == resolved.ResolvedCredentialRequirement(
        source=expected_credential_source,
        required=True,
        reference=expected_credential_reference,
    )
    assert config.routing_mode == "parasail_first"
    assert config.provider_routing == "default"
    assert config.service_endpoint == "https://broker.fixture.test/v1"
    assert config.fallback_provider is None
    assert config.fallback_model is None
    assert config.concurrency_limit == 8


def test_openrouter_qwen_primary_deepseek_only_preserves_routing_and_suppresses_fallback() -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        provider_llm="openrouter",
        model="qwen/qwen3.5-flash-02-23",
        selected_source="byok",
        fallback_selection_alias="deepseek_v4_flash",
        routing_mode="parasail_first",
        provider_routing="deepseek_only",
        broker_base_url="https://broker.fixture.test/v1",
    )
    translation_intent = runtime_resolution.derive_translation_runtime_intent_from_compatibility(
        provider_llm="openrouter",
        openrouter_model=openrouter_intent.model,
        openrouter_selected_source=openrouter_intent.selected_source,
        openrouter_provider_routing=openrouter_intent.provider_routing,
        concurrency_limit=8,
    )

    config = runtime_resolution.resolve_llm_config(
        runtime_resolution.RuntimeResolutionInput(
            translation=translation_intent,
            openrouter=openrouter_intent,
        )
    )

    assert translation_intent.model == runtime_resolution.TRANSLATION_MODEL_OPENROUTER_QWEN_35_FLASH
    assert config.provider == "openrouter"
    assert config.model == "qwen/qwen3.5-flash-02-23"
    assert config.provider_routing == "deepseek_only"
    assert config.routing_mode == "parasail_first"
    assert config.fallback_provider is None
    assert config.fallback_model is None
    assert config.fallback_credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_NONE,
        required=False,
        reference=None,
    )
    assert config.fallback_provider_routing is None


def test_missing_openrouter_source_defaults_to_byok_for_openrouter_provider() -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    raw_settings = {
        "provider": {"llm": "openrouter"},
        "openrouter": {
            "llm_model": "google/gemma-4-26b-a4b-it",
            "fallback_selection_alias": "none",
            "routing_mode": "latency",
            "provider_routing": "default",
            "broker_base_url": "https://broker.fixture.test/v1",
        },
        "gemini": {"llm_model": "gemini-3.1-flash-lite"},
        "qwen": {"llm_model": "qwen3.5-plus", "region": "beijing"},
        "deepseek": {"llm_model": "deepseek-v4-flash"},
        "llm": {"concurrency_limit": 3},
    }
    raw_openrouter = raw_settings["openrouter"]
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        provider_llm=raw_settings["provider"]["llm"],
        model=raw_openrouter["llm_model"],
        fallback_selection_alias=raw_openrouter["fallback_selection_alias"],
        routing_mode=raw_openrouter["routing_mode"],
        provider_routing=raw_openrouter["provider_routing"],
        broker_base_url=raw_openrouter["broker_base_url"],
    )
    translation_intent = runtime_resolution.derive_translation_runtime_intent_from_compatibility(
        provider_llm=raw_settings["provider"]["llm"],
        openrouter_model=openrouter_intent.model,
        openrouter_selected_source=openrouter_intent.selected_source,
        openrouter_provider_routing=openrouter_intent.provider_routing,
        gemini_model=raw_settings["gemini"]["llm_model"],
        qwen_model=raw_settings["qwen"]["llm_model"],
        deepseek_model=raw_settings["deepseek"]["llm_model"],
        concurrency_limit=raw_settings["llm"]["concurrency_limit"],
    )

    config = runtime_resolution.resolve_llm_config(
        runtime_resolution.RuntimeResolutionInput(
            translation=translation_intent,
            openrouter=openrouter_intent,
        )
    )

    assert openrouter_intent.model == "google/gemma-4-26b-a4b-it"
    assert openrouter_intent.selected_source == "byok"
    assert openrouter_intent.selection_alias == "gemma4_byok"
    assert translation_intent.model == runtime_resolution.TRANSLATION_MODEL_GEMMA4
    assert translation_intent.connection == runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER
    assert config.provider == "openrouter"
    assert config.model == "google/gemma-4-26b-a4b-it"
    assert config.credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_SECRET_STORE,
        required=True,
        reference="openrouter:byok",
    )
    assert config.routing_mode == "latency"
    assert config.provider_routing == "default"
    assert config.service_endpoint == "https://broker.fixture.test/v1"
    assert config.fallback_provider is None
    assert config.fallback_model is None
    assert config.fallback_credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_NONE,
        required=False,
        reference=None,
    )
    assert config.fallback_provider_routing is None
    assert config.concurrency_limit == 3


@pytest.mark.parametrize(
    "source_kwargs",
    [
        {},
        {"openrouter_selected_source": None},
    ],
)
def test_derive_translation_compatibility_defaults_missing_openrouter_source_to_byok(
    source_kwargs: dict[str, object],
) -> None:
    runtime_resolution = _runtime_resolution_module()

    translation_intent = runtime_resolution.derive_translation_runtime_intent_from_compatibility(
        provider_llm="openrouter",
        openrouter_model="google/gemma-4-26b-a4b-it",
        openrouter_provider_routing="default",
        concurrency_limit=3,
        **source_kwargs,
    )

    assert translation_intent.model == runtime_resolution.TRANSLATION_MODEL_GEMMA4
    assert translation_intent.connection == runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER
    assert translation_intent.concurrency_limit == 3


def test_missing_translation_direct_provider_compatibility_values_derive_exact_config() -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    raw_settings = {
        "provider": {"llm": "deepseek"},
        "openrouter": {
            "llm_model": "google/gemma-4-26b-a4b-it",
            "selected_source": "managed",
            "selection_alias": "gemma4_managed",
            "fallback_selection_alias": "qwen35_flash",
            "routing_mode": "latency",
            "provider_routing": "default",
            "broker_base_url": "https://broker.fixture.test/v1",
        },
        "gemini": {"llm_model": "gemini-3.1-flash-lite"},
        "qwen": {"llm_model": "qwen3.5-plus", "region": "singapore"},
        "deepseek": {"llm_model": "deepseek-v4-pro"},
        "llm": {"concurrency_limit": 6},
    }
    raw_openrouter = raw_settings["openrouter"]
    openrouter_intent = runtime_resolution.normalize_openrouter_runtime_intent(
        model=raw_openrouter["llm_model"],
        selected_source=raw_openrouter["selected_source"],
        selection_alias=raw_openrouter["selection_alias"],
        fallback_selection_alias=raw_openrouter["fallback_selection_alias"],
        routing_mode=raw_openrouter["routing_mode"],
        provider_routing=raw_openrouter["provider_routing"],
        broker_base_url=raw_openrouter["broker_base_url"],
    )
    translation_intent = runtime_resolution.derive_translation_runtime_intent_from_compatibility(
        provider_llm=raw_settings["provider"]["llm"],
        openrouter_model=openrouter_intent.model,
        openrouter_selected_source=openrouter_intent.selected_source,
        openrouter_provider_routing=openrouter_intent.provider_routing,
        gemini_model=raw_settings["gemini"]["llm_model"],
        qwen_model=raw_settings["qwen"]["llm_model"],
        deepseek_model=raw_settings["deepseek"]["llm_model"],
        concurrency_limit=raw_settings["llm"]["concurrency_limit"],
    )

    config = runtime_resolution.resolve_llm_config(
        runtime_resolution.RuntimeResolutionInput(
            translation=translation_intent,
            openrouter=openrouter_intent,
            direct=runtime_resolution.DirectProviderRuntimeIntent(
                deepseek_v4_pro_model=raw_settings["deepseek"]["llm_model"],
                qwen_region=raw_settings["qwen"]["region"],
            ),
        )
    )

    assert translation_intent.model == runtime_resolution.TRANSLATION_MODEL_DEEPSEEK_V4_PRO
    assert translation_intent.connection == runtime_resolution.TRANSLATION_CONNECTION_OFFICIAL_BYOK
    assert config.provider == "deepseek"
    assert config.model == "deepseek-v4-pro"
    assert config.credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_SECRET_STORE,
        required=True,
        reference="deepseek:byok",
    )
    assert config.base_url is None
    assert config.service_endpoint is None
    assert config.region is None
    assert config.routing_mode is None
    assert config.provider_routing is None
    assert config.fallback_provider is None
    assert config.fallback_model is None
    assert config.fallback_credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_NONE,
        required=False,
        reference=None,
    )
    assert config.concurrency_limit == 6


def test_resolved_output_uses_lookup_references_not_raw_secret_values() -> None:
    runtime_resolution = _runtime_resolution_module()
    resolved = _resolved_module()
    config = runtime_resolution.resolve_llm_config(
        _runtime_input(
            runtime_resolution,
            model=runtime_resolution.TRANSLATION_MODEL_GEMMA4,
            connection=runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER,
            openrouter=runtime_resolution.OpenRouterRuntimeIntent(
                selected_source=runtime_resolution.OPENROUTER_SOURCE_BYOK,
                fallback_selection_alias=(
                    runtime_resolution.OPENROUTER_FALLBACK_SELECTION_ALIAS_DEEPSEEK_V4_FLASH
                ),
            ),
        )
    )

    assert config.credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_SECRET_STORE,
        required=True,
        reference="openrouter:byok",
    )
    assert config.fallback_credential == resolved.ResolvedCredentialRequirement(
        source=resolved.CREDENTIAL_SOURCE_SECRET_STORE,
        required=True,
        reference="openrouter:byok",
    )
    assert config.credential.reference is not None
    assert "sk-" not in config.credential.reference
    assert "secret" not in config.credential.reference
