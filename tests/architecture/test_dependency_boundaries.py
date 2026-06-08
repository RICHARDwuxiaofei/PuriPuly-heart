from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PACKAGE_ROOT = REPO_ROOT / "src" / "puripuly_heart"
PACKAGE_NAME = "puripuly_heart"

SCHEMA_VALUES = "schema values"
MIGRATION_SERIALIZATION = "migration/serialization"
RESOLVED_DTOS = "resolved DTOs"
RUNTIME_RESOLUTION = "runtime resolution"
DOMAIN = "domain"
RUNTIME_OWNERS = "runtime owners"
ORCHESTRATOR = "orchestrator"
OVERLAY_CORE = "overlay core"
APP_SERVICES = "app services"
SERVICE_PORTS = "service ports"
ADAPTERS = "adapters"
OUTPUT_MESSAGE_OBSERVABILITY_PORTS = "output/message/observability ports"
PROVIDERS = "providers"
UI_ADAPTERS_RENDERERS = "UI adapters/renderers"

REQUIRED_LAYER_VOCABULARY = (
    SCHEMA_VALUES,
    MIGRATION_SERIALIZATION,
    RESOLVED_DTOS,
    RUNTIME_RESOLUTION,
    DOMAIN,
    RUNTIME_OWNERS,
    ORCHESTRATOR,
    OVERLAY_CORE,
    APP_SERVICES,
    SERVICE_PORTS,
    ADAPTERS,
    OUTPUT_MESSAGE_OBSERVABILITY_PORTS,
    PROVIDERS,
    UI_ADAPTERS_RENDERERS,
)


@dataclass(frozen=True, order=True, slots=True)
class ImportViolation:
    rule_id: str
    importer: str
    imported: str
    importer_layer: str
    imported_layer: str
    reason: str


@dataclass(frozen=True, slots=True)
class LayerRule:
    layer: str
    prefixes: tuple[str, ...]
    forbidden_layers: frozenset[str]
    rule_id: str
    reason: str


LAYER_RULES = (
    LayerRule(
        layer=SCHEMA_VALUES,
        prefixes=(
            "puripuly_heart.config.settings_vnext.schema",
            "puripuly_heart.config.audio_host_api",
            "puripuly_heart.config.llm_profiles",
        ),
        forbidden_layers=frozenset(
            {
                UI_ADAPTERS_RENDERERS,
                APP_SERVICES,
                ADAPTERS,
                PROVIDERS,
                OVERLAY_CORE,
                OUTPUT_MESSAGE_OBSERVABILITY_PORTS,
            }
        ),
        rule_id="schema-values-stay-pure",
        reason="schema/default value modules must not depend on UI, services, adapters, providers, overlay runtime, or observability ports",
    ),
    LayerRule(
        layer=MIGRATION_SERIALIZATION,
        prefixes=(
            "puripuly_heart.config.settings",
            "puripuly_heart.config.settings_vnext.migration",
            "puripuly_heart.config.settings_vnext.serialization",
            "puripuly_heart.config.settings_vnext.compat",
        ),
        forbidden_layers=frozenset(
            {
                UI_ADAPTERS_RENDERERS,
                APP_SERVICES,
                ADAPTERS,
                PROVIDERS,
                RUNTIME_OWNERS,
            }
        ),
        rule_id="migration-serialization-stays-compatible-and-pure",
        reason="settings migration and serialization must not import UI, app services, provider construction, SecretStore/Broker adapters, provider internals, or runtime state owners",
    ),
    LayerRule(
        layer=RESOLVED_DTOS,
        prefixes=("puripuly_heart.config.resolved",),
        forbidden_layers=frozenset(
            {
                UI_ADAPTERS_RENDERERS,
                APP_SERVICES,
                ADAPTERS,
                PROVIDERS,
                MIGRATION_SERIALIZATION,
            }
        ),
        rule_id="resolved-dtos-stay-pure",
        reason="resolved runtime DTOs must stay immutable/pure and avoid file I/O, SecretStore, providers, UI, Broker, or migration internals",
    ),
    LayerRule(
        layer=RUNTIME_RESOLUTION,
        prefixes=("puripuly_heart.config.runtime_resolution",),
        forbidden_layers=frozenset(
            {
                UI_ADAPTERS_RENDERERS,
                APP_SERVICES,
                ADAPTERS,
                PROVIDERS,
                MIGRATION_SERIALIZATION,
            }
        ),
        rule_id="runtime-resolution-stays-pure",
        reason="runtime resolution must consume canonical settings and resolved DTOs without file I/O, SecretStore, concrete providers, Flet UI, Broker HTTP, or migration internals",
    ),
    LayerRule(
        layer=DOMAIN,
        prefixes=("puripuly_heart.domain",),
        forbidden_layers=frozenset(
            {
                MIGRATION_SERIALIZATION,
                RUNTIME_RESOLUTION,
                UI_ADAPTERS_RENDERERS,
                APP_SERVICES,
                ADAPTERS,
                PROVIDERS,
            }
        ),
        rule_id="domain-stays-independent",
        reason="domain modules must not depend on config migration, UI, app services, adapters, runtime resolution, or concrete providers",
    ),
    LayerRule(
        layer=RUNTIME_OWNERS,
        prefixes=(
            "puripuly_heart.core.lifecycle",
            "puripuly_heart.core.runtime",
        ),
        forbidden_layers=frozenset(
            {
                MIGRATION_SERIALIZATION,
                UI_ADAPTERS_RENDERERS,
                APP_SERVICES,
                ADAPTERS,
                PROVIDERS,
            }
        ),
        rule_id="runtime-owners-use-ports",
        reason="runtime owners must coordinate through domain events, resolved DTOs, lifecycle/message/observability protocols, not app wiring, Flet UI, provider config parsing, or concrete adapters",
    ),
    LayerRule(
        layer=ORCHESTRATOR,
        prefixes=("puripuly_heart.core.orchestrator",),
        forbidden_layers=frozenset(
            {
                MIGRATION_SERIALIZATION,
                UI_ADAPTERS_RENDERERS,
                APP_SERVICES,
                ADAPTERS,
                PROVIDERS,
            }
        ),
        rule_id="orchestrator-avoids-product-adapters",
        reason="orchestrator modules must avoid Flet UI, concrete provider construction, settings migration internals, services, and product-output adapters",
    ),
    LayerRule(
        layer=OVERLAY_CORE,
        prefixes=("puripuly_heart.core.overlay",),
        forbidden_layers=frozenset({UI_ADAPTERS_RENDERERS}),
        rule_id="overlay-core-avoids-ui-renderers",
        reason="overlay core may use overlay protocol/value objects and observability ports, but not Flet controls, views, or desktop renderer defaults except through adapters",
    ),
    LayerRule(
        layer=APP_SERVICES,
        prefixes=("puripuly_heart.app.services",),
        forbidden_layers=frozenset(
            {
                MIGRATION_SERIALIZATION,
                UI_ADAPTERS_RENDERERS,
                ADAPTERS,
                PROVIDERS,
            }
        ),
        rule_id="app-services-use-ports",
        reason="app services own transactions through ports and DTOs, not UI controls, localized text, concrete providers, adapters, or migration internals",
    ),
    LayerRule(
        layer=SERVICE_PORTS,
        prefixes=("puripuly_heart.app.ports",),
        forbidden_layers=frozenset(
            {
                MIGRATION_SERIALIZATION,
                UI_ADAPTERS_RENDERERS,
                ADAPTERS,
                PROVIDERS,
            }
        ),
        rule_id="service-ports-stay-abstract",
        reason="service ports define protocols and DTOs only; they must not import concrete files, keyring/encrypted-file implementations, Flet, provider SDKs, adapters, or migration internals",
    ),
    LayerRule(
        layer=ADAPTERS,
        prefixes=(
            "puripuly_heart.app.adapters",
            "puripuly_heart.app.wiring",
            "puripuly_heart.core.managed_openrouter_broker_client",
            "puripuly_heart.core.osc",
            "puripuly_heart.core.runtime_logging",
            "puripuly_heart.core.storage",
        ),
        forbidden_layers=frozenset(
            {
                MIGRATION_SERIALIZATION,
                UI_ADAPTERS_RENDERERS,
            }
        ),
        rule_id="adapters-avoid-ui-and-migration-internals",
        reason="adapters may wrap concrete resources but must not depend on settings migration internals or UI controls unless explicitly UI-owned",
    ),
    LayerRule(
        layer=OUTPUT_MESSAGE_OBSERVABILITY_PORTS,
        prefixes=(
            "puripuly_heart.core.messages",
            "puripuly_heart.core.observability",
            "puripuly_heart.core.output",
        ),
        forbidden_layers=frozenset(
            {
                MIGRATION_SERIALIZATION,
                UI_ADAPTERS_RENDERERS,
                ADAPTERS,
                PROVIDERS,
            }
        ),
        rule_id="output-message-observability-ports-stay-abstract",
        reason="output/message/observability ports must avoid UI widgets, concrete OSC/overlay/log adapters, provider HTTP clients, and settings migration internals",
    ),
    LayerRule(
        layer=PROVIDERS,
        prefixes=("puripuly_heart.providers",),
        forbidden_layers=frozenset(
            {
                MIGRATION_SERIALIZATION,
                UI_ADAPTERS_RENDERERS,
                APP_SERVICES,
                ADAPTERS,
            }
        ),
        rule_id="providers-avoid-ui-settings-and-runtime-log-concretes",
        reason="providers may use provider ports, SDKs, and message/observability protocols, but not Flet UI, settings migration internals, app services, or concrete SessionRuntimeLoggingService-style adapters",
    ),
    LayerRule(
        layer=UI_ADAPTERS_RENDERERS,
        prefixes=("puripuly_heart.ui",),
        forbidden_layers=frozenset(
            {
                MIGRATION_SERIALIZATION,
                ADAPTERS,
                PROVIDERS,
            }
        ),
        rule_id="ui-adapters-avoid-provider-construction",
        reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
    ),
)

EXTERNAL_MODULE_LAYERS = {
    "flet": UI_ADAPTERS_RENDERERS,
}

KNOWN_ALLOWED_VIOLATIONS: frozenset[ImportViolation] = frozenset(
    {
        ImportViolation(
            rule_id="adapters-avoid-ui-and-migration-internals",
            importer="src/puripuly_heart/app/wiring.py",
            imported="puripuly_heart.config.settings",
            importer_layer="adapters",
            imported_layer="migration/serialization",
            reason="adapters may wrap concrete resources but must not depend on settings migration internals or UI controls unless explicitly UI-owned",
        ),
        ImportViolation(
            rule_id="adapters-avoid-ui-and-migration-internals",
            importer="src/puripuly_heart/core/managed_openrouter_broker_client.py",
            imported="puripuly_heart.config.settings",
            importer_layer="adapters",
            imported_layer="migration/serialization",
            reason="adapters may wrap concrete resources but must not depend on settings migration internals or UI controls unless explicitly UI-owned",
        ),
        ImportViolation(
            rule_id="migration-serialization-stays-compatible-and-pure",
            importer="src/puripuly_heart/config/settings.py",
            imported="puripuly_heart.ui.overlay_calibration",
            importer_layer="migration/serialization",
            imported_layer="UI adapters/renderers",
            reason="settings migration and serialization must not import UI, app services, provider construction, SecretStore/Broker adapters, provider internals, or runtime state owners",
        ),
        ImportViolation(
            rule_id="orchestrator-avoids-product-adapters",
            importer="src/puripuly_heart/core/orchestrator/hub.py",
            imported="puripuly_heart.core.runtime_logging",
            importer_layer="orchestrator",
            imported_layer="adapters",
            reason="orchestrator modules must avoid Flet UI, concrete provider construction, settings migration internals, services, and product-output adapters",
        ),
        ImportViolation(
            rule_id="orchestrator-avoids-product-adapters",
            importer="src/puripuly_heart/core/orchestrator/hub.py",
            imported="puripuly_heart.core.osc.chatbox_paginator",
            importer_layer="orchestrator",
            imported_layer="adapters",
            reason="orchestrator modules must avoid Flet UI, concrete provider construction, settings migration internals, services, and product-output adapters",
        ),
        ImportViolation(
            rule_id="overlay-core-avoids-ui-renderers",
            importer="src/puripuly_heart/core/overlay/presenter.py",
            imported="puripuly_heart.ui.overlay_calibration",
            importer_layer="overlay core",
            imported_layer="UI adapters/renderers",
            reason="overlay core may use overlay protocol/value objects and observability ports, but not Flet controls, views, or desktop renderer defaults except through adapters",
        ),
        ImportViolation(
            rule_id="providers-avoid-ui-settings-and-runtime-log-concretes",
            importer="src/puripuly_heart/providers/llm/deepseek.py",
            imported="puripuly_heart.core.runtime_logging",
            importer_layer="providers",
            imported_layer="adapters",
            reason="providers may use provider ports, SDKs, and message/observability protocols, but not Flet UI, settings migration internals, app services, or concrete SessionRuntimeLoggingService-style adapters",
        ),
        ImportViolation(
            rule_id="providers-avoid-ui-settings-and-runtime-log-concretes",
            importer="src/puripuly_heart/providers/llm/gemini.py",
            imported="puripuly_heart.core.runtime_logging",
            importer_layer="providers",
            imported_layer="adapters",
            reason="providers may use provider ports, SDKs, and message/observability protocols, but not Flet UI, settings migration internals, app services, or concrete SessionRuntimeLoggingService-style adapters",
        ),
        ImportViolation(
            rule_id="providers-avoid-ui-settings-and-runtime-log-concretes",
            importer="src/puripuly_heart/providers/llm/local_openai.py",
            imported="puripuly_heart.core.runtime_logging",
            importer_layer="providers",
            imported_layer="adapters",
            reason="providers may use provider ports, SDKs, and message/observability protocols, but not Flet UI, settings migration internals, app services, or concrete SessionRuntimeLoggingService-style adapters",
        ),
        ImportViolation(
            rule_id="providers-avoid-ui-settings-and-runtime-log-concretes",
            importer="src/puripuly_heart/providers/llm/openrouter.py",
            imported="puripuly_heart.config.settings",
            importer_layer="providers",
            imported_layer="migration/serialization",
            reason="providers may use provider ports, SDKs, and message/observability protocols, but not Flet UI, settings migration internals, app services, or concrete SessionRuntimeLoggingService-style adapters",
        ),
        ImportViolation(
            rule_id="providers-avoid-ui-settings-and-runtime-log-concretes",
            importer="src/puripuly_heart/providers/llm/openrouter.py",
            imported="puripuly_heart.core.runtime_logging",
            importer_layer="providers",
            imported_layer="adapters",
            reason="providers may use provider ports, SDKs, and message/observability protocols, but not Flet UI, settings migration internals, app services, or concrete SessionRuntimeLoggingService-style adapters",
        ),
        ImportViolation(
            rule_id="providers-avoid-ui-settings-and-runtime-log-concretes",
            importer="src/puripuly_heart/providers/llm/qwen.py",
            imported="puripuly_heart.core.runtime_logging",
            importer_layer="providers",
            imported_layer="adapters",
            reason="providers may use provider ports, SDKs, and message/observability protocols, but not Flet UI, settings migration internals, app services, or concrete SessionRuntimeLoggingService-style adapters",
        ),
        ImportViolation(
            rule_id="providers-avoid-ui-settings-and-runtime-log-concretes",
            importer="src/puripuly_heart/providers/llm/qwen_async.py",
            imported="puripuly_heart.core.runtime_logging",
            importer_layer="providers",
            imported_layer="adapters",
            reason="providers may use provider ports, SDKs, and message/observability protocols, but not Flet UI, settings migration internals, app services, or concrete SessionRuntimeLoggingService-style adapters",
        ),
        ImportViolation(
            rule_id="runtime-owners-use-ports",
            importer="src/puripuly_heart/core/runtime/peer_channel.py",
            imported="puripuly_heart.app.wiring",
            importer_layer="runtime owners",
            imported_layer="adapters",
            reason="runtime owners must coordinate through domain events, resolved DTOs, lifecycle/message/observability protocols, not app wiring, Flet UI, provider config parsing, or concrete adapters",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/app.py",
            imported="puripuly_heart.config.settings",
            importer_layer="UI adapters/renderers",
            imported_layer="migration/serialization",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.app.wiring",
            importer_layer="UI adapters/renderers",
            imported_layer="adapters",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.config.settings",
            importer_layer="UI adapters/renderers",
            imported_layer="migration/serialization",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.core.managed_openrouter_broker_client",
            importer_layer="UI adapters/renderers",
            imported_layer="adapters",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.core.runtime_logging",
            importer_layer="UI adapters/renderers",
            imported_layer="adapters",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.core.osc.chatbox_paginator",
            importer_layer="UI adapters/renderers",
            imported_layer="adapters",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.core.osc.receiver",
            importer_layer="UI adapters/renderers",
            imported_layer="adapters",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.core.osc.udp_sender",
            importer_layer="UI adapters/renderers",
            imported_layer="adapters",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.providers.llm.deepseek",
            importer_layer="UI adapters/renderers",
            imported_layer="providers",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.providers.llm.gemini",
            importer_layer="UI adapters/renderers",
            imported_layer="providers",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.providers.llm.openrouter",
            importer_layer="UI adapters/renderers",
            imported_layer="providers",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.providers.llm.qwen",
            importer_layer="UI adapters/renderers",
            imported_layer="providers",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.providers.llm.qwen_async",
            importer_layer="UI adapters/renderers",
            imported_layer="providers",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.providers.stt.deepgram",
            importer_layer="UI adapters/renderers",
            imported_layer="providers",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.providers.stt.local_qwen_sherpa",
            importer_layer="UI adapters/renderers",
            imported_layer="providers",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.providers.stt.soniox",
            importer_layer="UI adapters/renderers",
            imported_layer="providers",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/desktop_overlay.py",
            imported="puripuly_heart.config.settings",
            importer_layer="UI adapters/renderers",
            imported_layer="migration/serialization",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/event_bridge.py",
            imported="puripuly_heart.core.runtime_logging",
            importer_layer="UI adapters/renderers",
            imported_layer="adapters",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/views/settings.py",
            imported="puripuly_heart.app.wiring",
            importer_layer="UI adapters/renderers",
            imported_layer="adapters",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
        ImportViolation(
            rule_id="ui-adapters-avoid-provider-construction",
            importer="src/puripuly_heart/ui/views/settings.py",
            imported="puripuly_heart.config.settings",
            importer_layer="UI adapters/renderers",
            imported_layer="migration/serialization",
            reason="UI adapters/renderers may depend on app services, snapshots, i18n, and rendered log entries, not migration internals, provider construction, or concrete resource wiring",
        ),
    }
)


def _module_name_for_path(path: Path) -> str:
    relative = path.relative_to(SOURCE_PACKAGE_ROOT).with_suffix("")
    parts = (PACKAGE_NAME, *relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_parts_for_importer(importer_module: str, importer_path: Path) -> list[str]:
    parts = importer_module.split(".")
    if importer_path.name != "__init__.py":
        parts = parts[:-1]
    return parts


def _absolute_import_from_module(
    importer_module: str,
    importer_path: Path,
    node: ast.ImportFrom,
) -> str | None:
    if node.level == 0:
        return node.module

    package_parts = _package_parts_for_importer(importer_module, importer_path)
    if node.level > len(package_parts) + 1:
        return None

    base_parts = package_parts[: len(package_parts) - node.level + 1]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _internal_module_names() -> frozenset[str]:
    return frozenset(_module_name_for_path(path) for path in SOURCE_PACKAGE_ROOT.rglob("*.py"))


def _is_internal_module(module: str) -> bool:
    return module == PACKAGE_NAME or module.startswith(f"{PACKAGE_NAME}.")


def _layer_root_module_names() -> frozenset[str]:
    return frozenset(prefix for rule in LAYER_RULES for prefix in rule.prefixes)


def _imported_modules(
    importer_module: str,
    importer_path: Path,
    internal_modules: frozenset[str],
) -> Iterator[str]:
    tree = ast.parse(importer_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
            continue

        if not isinstance(node, ast.ImportFrom):
            continue

        module = _absolute_import_from_module(importer_module, importer_path, node)
        if module is None:
            continue

        if not _is_internal_module(module):
            yield module
            continue

        for alias in node.names:
            candidate = f"{module}.{alias.name}"
            if candidate in internal_modules or candidate in _layer_root_module_names():
                yield candidate
            else:
                yield module


def _layer_for_module(module: str) -> str | None:
    for external_module, layer in EXTERNAL_MODULE_LAYERS.items():
        if module == external_module or module.startswith(f"{external_module}."):
            return layer

    for prefix, layer in _layer_prefixes_by_specificity():
        if module == prefix or module.startswith(f"{prefix}."):
            return layer

    return None


def _layer_prefixes_by_specificity() -> tuple[tuple[str, str], ...]:
    prefixes = [(prefix, rule.layer) for rule in LAYER_RULES for prefix in rule.prefixes]
    return tuple(sorted(prefixes, key=lambda entry: len(entry[0]), reverse=True))


def _rule_for_layer(layer: str) -> LayerRule:
    for rule in LAYER_RULES:
        if rule.layer == layer:
            return rule
    raise AssertionError(f"no dependency rule for layer {layer!r}")


def _relative_repo_path(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _dependency_violations() -> frozenset[ImportViolation]:
    internal_modules = _internal_module_names()
    violations: set[ImportViolation] = set()

    for importer_path in sorted(SOURCE_PACKAGE_ROOT.rglob("*.py")):
        importer_module = _module_name_for_path(importer_path)
        importer_layer = _layer_for_module(importer_module)
        if importer_layer is None:
            continue

        rule = _rule_for_layer(importer_layer)
        for imported_module in sorted(
            set(_imported_modules(importer_module, importer_path, internal_modules))
        ):
            imported_layer = _layer_for_module(imported_module)
            if imported_layer is None:
                continue
            if imported_layer not in rule.forbidden_layers:
                continue

            violations.add(
                ImportViolation(
                    rule_id=rule.rule_id,
                    importer=_relative_repo_path(importer_path),
                    imported=imported_module,
                    importer_layer=importer_layer,
                    imported_layer=imported_layer,
                    reason=rule.reason,
                )
            )

    return frozenset(violations)


def _format_violations(violations: list[ImportViolation]) -> str:
    if not violations:
        return "  <none>"

    return "\n".join(
        "  ImportViolation(\n"
        f'      rule_id="{violation.rule_id}",\n'
        f'      importer="{violation.importer}",\n'
        f'      imported="{violation.imported}",\n'
        f'      importer_layer="{violation.importer_layer}",\n'
        f'      imported_layer="{violation.imported_layer}",\n'
        f'      reason="{violation.reason}",\n'
        "  ),"
        for violation in violations
    )


def test_dependency_rule_vocabulary_distinguishes_required_layers() -> None:
    assert tuple(rule.layer for rule in LAYER_RULES) == REQUIRED_LAYER_VOCABULARY
    assert {rule.layer for rule in LAYER_RULES} == set(REQUIRED_LAYER_VOCABULARY)


def test_migration_serialization_forbids_runtime_owner_dependencies() -> None:
    assert RUNTIME_OWNERS in _rule_for_layer(MIGRATION_SERIALIZATION).forbidden_layers


def test_concrete_osc_modules_classify_as_adapters() -> None:
    assert _layer_for_module("puripuly_heart.core.osc.chatbox_paginator") == ADAPTERS
    assert _layer_for_module("puripuly_heart.core.osc.receiver") == ADAPTERS
    assert _layer_for_module("puripuly_heart.core.osc.udp_sender") == ADAPTERS


def test_current_concrete_osc_imports_are_adapter_boundary_violations() -> None:
    orchestrator_rule = _rule_for_layer(ORCHESTRATOR)
    ui_rule = _rule_for_layer(UI_ADAPTERS_RENDERERS)
    expected = {
        ImportViolation(
            rule_id=orchestrator_rule.rule_id,
            importer="src/puripuly_heart/core/orchestrator/hub.py",
            imported="puripuly_heart.core.osc.chatbox_paginator",
            importer_layer=ORCHESTRATOR,
            imported_layer=ADAPTERS,
            reason=orchestrator_rule.reason,
        ),
        ImportViolation(
            rule_id=ui_rule.rule_id,
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.core.osc.chatbox_paginator",
            importer_layer=UI_ADAPTERS_RENDERERS,
            imported_layer=ADAPTERS,
            reason=ui_rule.reason,
        ),
        ImportViolation(
            rule_id=ui_rule.rule_id,
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.core.osc.receiver",
            importer_layer=UI_ADAPTERS_RENDERERS,
            imported_layer=ADAPTERS,
            reason=ui_rule.reason,
        ),
        ImportViolation(
            rule_id=ui_rule.rule_id,
            importer="src/puripuly_heart/ui/controller.py",
            imported="puripuly_heart.core.osc.udp_sender",
            importer_layer=UI_ADAPTERS_RENDERERS,
            imported_layer=ADAPTERS,
            reason=ui_rule.reason,
        ),
    }

    assert expected <= _dependency_violations()


def test_absolute_from_import_resolves_layer_root_namespace_candidates(
    tmp_path: Path,
) -> None:
    importer_path = tmp_path / "importer.py"
    importer_path.write_text("from puripuly_heart import ui\n", encoding="utf-8")

    imported_modules = set(
        _imported_modules(
            "puripuly_heart.config.settings",
            importer_path,
            _internal_module_names(),
        )
    )

    assert "puripuly_heart.ui" in imported_modules
    assert _layer_for_module("puripuly_heart.ui") == UI_ADAPTERS_RENDERERS


def test_dependency_boundary_allowlist_matches_current_violations() -> None:
    actual = _dependency_violations()

    unexpected = sorted(actual - KNOWN_ALLOWED_VIOLATIONS)
    stale = sorted(KNOWN_ALLOWED_VIOLATIONS - actual)

    assert not unexpected and not stale, (
        "Dependency boundary allowlist mismatch. Add only current known exceptions "
        "to KNOWN_ALLOWED_VIOLATIONS, and remove entries as refactors eliminate them.\n"
        "Unexpected violations:\n"
        f"{_format_violations(unexpected)}\n"
        "Stale allowlist entries:\n"
        f"{_format_violations(stale)}"
    )
