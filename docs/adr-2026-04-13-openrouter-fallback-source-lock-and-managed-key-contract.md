# ADR: OpenRouter Fallback Source Lock And Managed-Key Contract

- Status: Accepted
- Date: 2026-04-13
- Work ref: `openrouter-managed-fallback-defaults`

## Context

The OpenRouter managed rollout now makes OpenRouter the default LLM path for new users. The previous fallback set still exposed Gemma 4 as a fallback choice even though Gemma 4 is now the default managed main model. That made the fallback menu redundant and kept legacy persisted fallback values alive past their intended lifetime.

## Decision

### 1. New-user defaults

- `AppSettings()` defaults `provider.llm` to `openrouter`
- the default OpenRouter main selection is managed Gemma 4
- the default OpenRouter fallback is Gemini 2.5 Flash Lite

### 2. Supported fallback choices

The supported OpenRouter fallback aliases are exactly:

- `none`
- `gemini25_flash_lite`
- `qwen35_flash`

Gemma 4 is no longer a selectable fallback option.

### 3. Compatibility rules

- persisted `fallback_selection_alias = gemma4` migrates to `gemini25_flash_lite`
- missing or invalid fallback aliases normalize to `gemini25_flash_lite`
- `selection_alias = null` remains valid only for legacy or inactive OpenRouter states and is no longer the new-user default

## Consequences

- new installs land on managed OpenRouter Gemma 4 without requiring a manual LLM switch
- fallback UI, registries, enums, and locale strings must stay aligned with the reduced fallback set
- legacy settings continue loading without losing OpenRouter compatibility

## References

- `src/puripuly_heart/config/llm_profiles.py`
- `src/puripuly_heart/config/settings.py`
- `src/puripuly_heart/ui/views/settings.py`
