# vNext General LLM Fallback Plan

Status: planned
Date: 2026-07-03
Worktree baseline: `vnext-refactoring-architecture`

## Summary

The temporary `dev` implementation broadened the OpenRouter fallback behavior into a more general LLM fallback path. In `vnext-refactoring-architecture`, fallback should be formalized as an optional second LLM branch instead of an OpenRouter-specific setting.

The intended behavior is:

```text
primary translation branch
  model + translation connection

optional fallback translation branch
  fixed UI preset → model + translation connection
```

The UI should keep dev parity for now: show fixed fallback combinations in the existing fallback picker flow. Do not implement a free-form fallback model picker plus fallback connection picker yet.

## Boundaries

The work changes these architecture boundaries:

```text
settings/persistence
runtime resolution
adapter wiring
UI rendering
```

Keep persisted user intent, resolved runtime config, and runtime-only provider instances separate.

## Decisions

### D1. Fallback is a second LLM branch

Fallback is no longer an OpenRouter-only option. It is an optional second translation branch with the same conceptual shape as the primary branch:

```text
translation model
translation connection
```

The UI may expose fixed presets, but runtime resolution should receive and resolve the branch as model plus connection, not as an OpenRouter fallback alias.

### D2. Remove Qwen 3.5 Flash fallback

Qwen 3.5 Flash is no longer a fallback option.

Remove it from:

```text
fallback preset catalog
fallback UI options
OpenRouter fallback alias handling after migration
runtime fallback tests
```

Do not remove unrelated Qwen main-model or STT behavior as part of this work.

### D3. No compatibility layer after migration

Do not keep old fallback fields as live compatibility surfaces. Read old shapes only during migration, project them into the new canonical schema, and serialize only the new schema afterward.

Old values to absorb during migration include:

```text
openrouter.fallback_selection_alias
translation.fallback_selection_alias from the temporary dev shape, if present
intent.translation.openrouter_fallback_selection_alias from earlier vNext shape
```

### D4. UI remains dev parity

The UI should not be redesigned yet. Keep the existing dev-style fallback picker that presents fixed combinations.

The label should be generic fallback copy, not OpenRouter-only copy. The stored value should be the new fallback branch intent.

## Canonical Settings Shape

Add a canonical fallback intent under translation intent:

```python
@dataclass(frozen=True, slots=True)
class TranslationFallbackIntent:
    enabled: bool = False
    model: str = "deepseek_v4_flash"
    connection: str = "official_byok"


@dataclass(frozen=True, slots=True)
class TranslationIntent:
    model: str = "gemma4"
    connection: str = "managed"
    connection_history: dict[str, str] = field(default_factory=...)
    concurrency_limit: int = 5
    fallback: TranslationFallbackIntent = field(default_factory=TranslationFallbackIntent)

    openrouter_broker_base_url: str = DEFAULT_OPENROUTER_BROKER_BASE_URL
    openrouter_routing_mode: str = "latency"
    openrouter_model: str = "google/gemma-4-26b-a4b-it"
    openrouter_selected_source: str = "managed"
    openrouter_selection_alias: str | None = "gemma4_managed"
    openrouter_provider_routing: str = "default"
    qwen: QwenTranslationIntent = field(default_factory=QwenTranslationIntent)
    cerebras: CerebrasTranslationIntent = field(default_factory=CerebrasTranslationIntent)
```

Remove from the canonical schema:

```text
TranslationIntent.openrouter_fallback_selection_alias
OpenRouter fallback selection as a persisted canonical setting
```

## Migration Rules

Migration should be one-way into `TranslationFallbackIntent`.

### No fallback

```text
openrouter.fallback_selection_alias = none
```

or missing legacy fallback:

```python
TranslationFallbackIntent(enabled=False)
```

### Legacy OpenRouter DeepSeek fallback

```text
openrouter.fallback_selection_alias = deepseek_v4_flash
```

If the legacy OpenRouter selected source is managed:

```python
TranslationFallbackIntent(
    enabled=True,
    model="deepseek_v4_flash",
    connection="managed",
)
```

If the legacy OpenRouter selected source is BYOK:

```python
TranslationFallbackIntent(
    enabled=True,
    model="deepseek_v4_flash",
    connection="openrouter",
)
```

### Legacy China DeepSeek fallback

```text
openrouter.fallback_selection_alias = deepseek_v4_flash_china
```

becomes:

```python
TranslationFallbackIntent(
    enabled=True,
    model="deepseek_v4_flash",
    connection="managed_china",
)
```

### Legacy Qwen 3.5 Flash fallback

```text
openrouter.fallback_selection_alias = qwen35_flash
```

becomes disabled fallback:

```python
TranslationFallbackIntent(enabled=False)
```

Do not silently remap this to DeepSeek. Qwen 3.5 Flash is removed from fallback options, and choosing a replacement model would change cost and quality semantics without explicit user intent.

### Temporary dev generic fallback aliases

If the migrated source contains the temporary dev shape:

```text
translation.fallback_selection_alias
```

map known aliases to the new branch intent and drop the alias from the serialized output.

Expected examples:

```text
deepseek_v4_flash_official
  → enabled=True, model=deepseek_v4_flash, connection=official_byok

openrouter_deepseek_v4_flash
  → enabled=True, model=deepseek_v4_flash, connection=openrouter

openrouter_gemma4_26b_a4b
  → enabled=True, model=gemma4, connection=openrouter

cerebras_gemma4_31b
  → enabled=True, model=gemma4_31b_cerebras, connection=official_byok
```

Unknown fallback aliases should migrate to disabled fallback rather than guessing.

## Runtime Resolution Plan

Replace the flat fallback fields on `ResolvedLLMConfig` with explicit branch DTOs.

```python
@dataclass(frozen=True, slots=True)
class ResolvedLLMTarget:
    provider: str
    model: str
    credential: ResolvedCredentialRequirement
    base_url: str | None = None
    service_endpoint: str | None = None
    region: str | None = None
    routing_mode: str | None = None
    provider_routing: str | None = None
    provider_options: Mapping[str, ResolvedOptionValue] = field(default_factory=...)


@dataclass(frozen=True, slots=True)
class ResolvedLLMFallbackPlan:
    target: ResolvedLLMTarget
    timeout_ms: int = 2000
    loser_grace_ms: int = 50
    force_managed_wrapper: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedLLMConfig:
    primary: ResolvedLLMTarget
    fallback: ResolvedLLMFallbackPlan | None = None
    concurrency_limit: int = 5
```

Resolver flow:

```text
resolve primary translation branch
if fallback.enabled:
  resolve fallback translation branch using the same target resolver
  if fallback target is not equivalent to primary target:
    attach ResolvedLLMFallbackPlan
```

Remove these fields from the resolved config contract:

```text
fallback_provider
fallback_model
fallback_credential
fallback_provider_routing
```

Fallback target resolution must reuse primary target resolution rules for:

```text
OpenRouter managed/BYOK credentials
OpenRouter provider routing
DeepSeek official BYOK
Cerebras official BYOK
Gemini official BYOK/OpenRouter
Local LLM/Ollama
```

## Fixed Fallback Presets

The UI should present fixed presets, but these presets are only UI/input conveniences. The canonical saved value is `TranslationFallbackIntent`.

Initial preset set:

```text
None
DeepSeek V4 Flash · Official BYOK
DeepSeek V4 Flash · OpenRouter
Gemma 4 26B · OpenRouter
Cerebras Gemma 4 31B · Official BYOK
```

Represent them as branch values:

```python
FallbackPreset(enabled=False)

FallbackPreset(
    model="deepseek_v4_flash",
    connection="official_byok",
)

FallbackPreset(
    model="deepseek_v4_flash",
    connection="openrouter",
)

FallbackPreset(
    model="gemma4",
    connection="openrouter",
)

FallbackPreset(
    model="gemma4_31b_cerebras",
    connection="official_byok",
)
```

Do not include Qwen 3.5 Flash.

Managed/China fallback values may still exist through migration. Do not expose new managed fallback presets in the first UI pass unless product approval explicitly expands the preset list.

## UI Plan

Keep dev parity and avoid a broader UI redesign.

Current direction:

```text
existing fallback card/modal flow
fixed fallback preset list
generic fallback labels
no separate fallback model picker
no separate fallback connection picker
```

The fallback card should display:

```text
Fallback: Off
```

or:

```text
Fallback: DeepSeek V4 Flash · Official BYOK
Fallback: DeepSeek V4 Flash · OpenRouter
Fallback: Gemma 4 26B · OpenRouter
Fallback: Cerebras Gemma 4 31B · Official BYOK
```

Selection behavior:

```text
None preset
  → fallback.enabled=False

Any enabled preset
  → fallback.enabled=True
  → fallback.model=<preset model>
  → fallback.connection=<preset connection>
```

API key visibility must consider both primary and fallback branches:

```text
DeepSeek official BYOK fallback → show DeepSeek key
Cerebras official BYOK fallback → show Cerebras key
OpenRouter fallback → show OpenRouter BYOK key or managed controls according to resolved branch
Local LLM fallback, if ever preset-exposed → show local LLM connection inputs as needed
```

All user-facing copy changes require i18n keys and locale bundle updates.

## Adapter Wiring Plan

Move provider construction to a target materializer:

```python
def _provider_from_resolved_target(target: ResolvedLLMTarget, ...) -> LLMProvider:
    ...
```

Then wire fallback provider-neutrally:

```python
primary = _provider_from_resolved_target(config.primary)

if config.fallback is not None:
    primary = FallbackRacingLLMProvider(
        primary=primary,
        fallback=_LazyFactoryLLMProvider(
            factory=lambda: _provider_from_resolved_target(config.fallback.target)
        ),
        fallback_timeout_ms=config.fallback.timeout_ms,
        loser_grace_ms=config.fallback.loser_grace_ms,
        runtime_logging=runtime_logging,
    )

return SemaphoreLLMProvider(
    inner=primary,
    semaphore=asyncio.Semaphore(config.concurrency_limit),
)
```

OpenRouter managed fallback must preserve the existing fallback-specific managed release service behavior. Represent any forced managed-wrapper behavior in the resolved fallback plan instead of hiding it in OpenRouter-only wiring.

## Removal Checklist

Remove or replace:

```text
OpenRouterRuntimeIntent.fallback_selection_alias
ResolvedLLMConfig.fallback_provider
ResolvedLLMConfig.fallback_model
ResolvedLLMConfig.fallback_credential
ResolvedLLMConfig.fallback_provider_routing
TranslationIntent.openrouter_fallback_selection_alias
OpenRouter-specific fallback UI naming and persistence
Qwen 3.5 Flash fallback preset
```

Keep out of scope unless separately approved:

```text
Qwen main translation model behavior
Qwen STT/ASR behavior
free-form fallback model and connection UI
new managed fallback presets
```

## Verification Plan

Recommended test areas:

```text
tests/config/test_runtime_resolution.py
tests/config/test_resolved_runtime_dtos.py
tests/config/test_public_compatibility_surfaces.py
tests/app/test_wiring_providers.py
settings vNext migration tests
settings UI fallback picker tests
```

Coverage to add or update:

```text
fallback disabled resolves to no fallback plan
DeepSeek official fallback resolves to DeepSeek provider target
Cerebras official fallback resolves to Cerebras provider target
OpenRouter DeepSeek fallback resolves to OpenRouter provider target
OpenRouter Gemma fallback resolves to OpenRouter provider target
primary-equivalent fallback is omitted
legacy qwen35_flash fallback migrates to disabled fallback
legacy OpenRouter fallback aliases serialize only as translation.fallback
UI fixed preset list excludes Qwen 3.5 Flash
UI preset selection writes TranslationFallbackIntent fields
API key visibility accounts for fallback branch requirements
```

## Suggested Implementation Order

1. Add `TranslationFallbackIntent` and migration projection.
2. Replace flat resolved fallback fields with branch DTOs.
3. Extract shared translation target resolver.
4. Convert adapter wiring to materialize `ResolvedLLMTarget` objects.
5. Convert UI fallback picker to fixed generic presets while keeping dev parity.
6. Update i18n keys and tests.
7. Run targeted config/runtime/wiring/UI tests.
