from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import UUID


def _value(value: object) -> object:
    return getattr(value, "value", value)


def _settings_trace() -> dict[str, object]:
    from puripuly_heart.config.settings import AppSettings

    settings = AppSettings()
    translation = settings.translation
    fallback = getattr(translation, "fallback_selection_alias", None)
    normalized_fallback = _value(fallback)
    if normalized_fallback is None:
        normalized_fallback = "none"
    return {
        "provider": _value(settings.provider.llm),
        "model": _value(getattr(translation, "model", None)),
        "connection": _value(getattr(translation, "connection", None)),
        "fallback": normalized_fallback,
    }


def _prompt_trace() -> dict[str, object]:
    from puripuly_heart.config import prompts

    with tempfile.TemporaryDirectory(prefix="puripuly-prompt-probe-") as directory:
        root = Path(directory)
        (root / "translation_prompt.md").write_text("SHARED_TRANSLATION", encoding="utf-8")
        (root / "cerebras.md").write_text("CEREBRAS_NAMED", encoding="utf-8")
        previous = os.environ.get("PURIPULY_HEART_PROMPTS_DIR")
        os.environ["PURIPULY_HEART_PROMPTS_DIR"] = str(root)
        prompts._reset_prompt_cache_for_tests()
        try:
            return {
                "cerebras": prompts.load_prompt_for_provider("cerebras"),
                "unknown": prompts.load_prompt_for_provider("unknown"),
            }
        finally:
            prompts._reset_prompt_cache_for_tests()
            if previous is None:
                os.environ.pop("PURIPULY_HEART_PROMPTS_DIR", None)
            else:
                os.environ["PURIPULY_HEART_PROMPTS_DIR"] = previous


def _first_run_trace() -> dict[str, object]:
    from puripuly_heart import main as main_module
    from puripuly_heart.config import settings as settings_module

    results: dict[str, object] = {}
    original = settings_module.detect_system_locale
    try:
        for name, locale in (("non_zh_cn", "ko_KR"), ("zh_cn", "zh_CN")):
            settings_module.detect_system_locale = lambda locale=locale: locale
            with tempfile.TemporaryDirectory(prefix="puripuly-first-run-probe-") as directory:
                path = Path(directory) / "settings.json"
                loader = main_module._load_settings_or_default
                kwargs = (
                    {"allow_stable_settings_import": False}
                    if "allow_stable_settings_import" in inspect.signature(loader).parameters
                    else {}
                )
                loaded = loader(path, **kwargs)
            translation = getattr(getattr(loaded, "intent", loaded), "translation")
            fallback = getattr(translation, "fallback", None)
            results[name] = {
                "model": _value(getattr(translation, "model", None)),
                "connection": _value(getattr(translation, "connection", None)),
                "fallback": _value(
                    getattr(fallback, "selection_alias", None)
                    if fallback is not None
                    else getattr(translation, "fallback_selection_alias", None)
                ),
            }
    finally:
        settings_module.detect_system_locale = original
    return results


def _prompt_policy_trace(source_root: Path) -> dict[str, object]:
    content = (source_root / "prompts" / "translation_prompt.md").read_bytes()
    return {"sha256": hashlib.sha256(content).hexdigest()}


def _source_manifest(source_root: Path) -> str:
    paths = (
        "prompts/translation_prompt.md",
        "src/puripuly_heart/config/prompts.py",
        "src/puripuly_heart/config/settings.py",
        "src/puripuly_heart/core/llm/fallback_racing.py",
        "src/puripuly_heart/main.py",
    )
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((source_root / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


async def _fallback_trace() -> dict[str, object]:
    from puripuly_heart.core.llm.fallback_racing import FallbackRacingLLMProvider
    from puripuly_heart.domain.models import Translation

    calls: list[str] = []

    class Provider:
        def __init__(self, name: str, *, error: bool = False, delay: float = 0.0) -> None:
            self.name = name
            self.error = error
            self.delay = delay

        async def translate(self, **kwargs: object) -> Translation:
            calls.append(self.name)
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error:
                raise RuntimeError(f"{self.name}-failure")
            return Translation(utterance_id=kwargs["utterance_id"], text=self.name)

        async def close(self) -> None:
            return None

    params = {
        "utterance_id": UUID(int=1),
        "text": "fixture",
        "system_prompt": "fixture",
        "source_language": "ko",
        "target_language": "en",
    }
    error_race = FallbackRacingLLMProvider(
        primary=Provider("primary_error", error=True),
        fallback=Provider("fallback_after_error"),
        fallback_timeout_ms=100,
    )
    error_result = await error_race.translate(**params)
    await error_race.close()
    error_calls = tuple(calls)
    calls.clear()
    timeout_race = FallbackRacingLLMProvider(
        primary=Provider("primary_slow", delay=0.1),
        fallback=Provider("fallback_after_timeout"),
        fallback_timeout_ms=1,
    )
    timeout_result = await timeout_race.translate(**params)
    await timeout_race.close()
    return {
        "primary_error": {"result": error_result.text, "calls": error_calls},
        "timeout": {"result": timeout_result.text, "calls": tuple(calls)},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    sys.path.insert(0, str(source_root / "src"))
    trace = {
        "provenance": {"source_manifest_sha256": _source_manifest(source_root)},
        "settings_defaults": _settings_trace(),
        "first_run_defaults": _first_run_trace(),
        "prompt_policy": _prompt_policy_trace(source_root),
        "prompt_routing": _prompt_trace(),
        "fallback_triggers": asyncio.run(_fallback_trace()),
    }
    print(json.dumps(trace, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
