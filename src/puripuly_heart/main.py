from __future__ import annotations

import argparse
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext


def configure_main_logging():
    from puripuly_heart.core.runtime_logging import configure_main_logging as configure

    return configure()


def default_settings_path() -> Path:
    from puripuly_heart.config.paths import default_settings_path as resolve

    return resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="puripuly-heart")
    parser.add_argument("--version", action="store_true", help="Print version and exit")

    parser.add_argument(
        "--config",
        type=Path,
        default=argparse.SUPPRESS,
        help="Path to settings JSON (default: vNext user config dir)",
    )
    parser.add_argument(
        "--debug-ui-preview",
        action="store_true",
        default=False,
        help="Show developer-only GUI preview controls for hidden UI states",
    )

    sub = parser.add_subparsers(dest="command")

    desktop_overlay = sub.add_parser(
        "run-desktop-overlay",
        help="Run the desktop Flet overlay renderer",
    )
    desktop_overlay.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to overlay launch manifest JSON",
    )
    sub.add_parser(
        "run-desktop-overlay-preview",
        help="Run the desktop Flet overlay preview",
    )
    desktop_repro = sub.add_parser(
        "run-desktop-overlay-repro",
        help="Run Desktop overlay diagnostic repro",
    )
    desktop_repro.add_argument("--cycles", type=int, default=100)
    desktop_repro.add_argument("--dwell-ms", type=int, default=150)
    desktop_repro.add_argument("--output-dir", type=Path, required=True)
    verify_desktop_repro = sub.add_parser(
        "verify-desktop-overlay-repro",
        help="Verify Desktop overlay repro artifacts",
    )
    verify_desktop_repro.add_argument("--output-dir", type=Path, required=True)

    sub.add_parser(
        "local-qwen-runtime-check",
        help="Verify the Local Qwen Windows runtime DLL directory",
    )
    sub.add_parser(
        "soxr-runtime-check",
        help="Verify the packaged soxr runtime contract and smoke resample",
    )

    run_gui = sub.add_parser("run-gui", help="Run the Graphical User Interface (Flet)")
    run_gui.add_argument(
        "--debug-ui-preview",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Show developer-only GUI preview controls for hidden UI states",
    )

    return parser


def run_local_qwen_runtime_check() -> int:
    from puripuly_heart.app.local_qwen_runtime_check import run_local_qwen_runtime_check as run

    return run()


def run_soxr_runtime_check() -> int:
    from puripuly_heart.app.soxr_runtime_check import run_soxr_runtime_check as run

    return run()


def _run_gui(
    config_path: Path,
    *,
    debug_ui_preview: bool,
    allow_stable_settings_import: bool,
) -> int:
    import flet as ft

    from puripuly_heart.ui.app import main_gui
    from puripuly_heart.ui.fonts import assets_dir

    async def _target(page: ft.Page):
        kwargs = {
            "config_path": config_path,
            "debug_ui_preview": debug_ui_preview,
        }
        parameters = inspect.signature(main_gui).parameters
        if "allow_stable_settings_import" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        ):
            kwargs["allow_stable_settings_import"] = allow_stable_settings_import
        return await main_gui(page, **kwargs)

    ft.app(target=_target, assets_dir=str(assets_dir()))
    return 0


def _run_desktop_overlay(config_path: Path) -> int:
    from puripuly_heart.ui.desktop_overlay import main as desktop_overlay_main

    return desktop_overlay_main(["--config", str(config_path)])


def _run_desktop_overlay_preview() -> int:
    from puripuly_heart.ui.desktop_overlay import main as desktop_overlay_main

    return desktop_overlay_main(["--preview"])


def _run_desktop_overlay_repro(*, cycles: int, dwell_ms: int, output_dir: Path) -> int:
    from puripuly_heart.ui.desktop_overlay_repro import run_desktop_overlay_repro

    return run_desktop_overlay_repro(cycles=cycles, dwell_ms=dwell_ms, output_dir=output_dir)


def _verify_desktop_overlay_repro(*, output_dir: Path) -> int:
    from puripuly_heart.core.desktop_overlay_repro_artifacts import verify_desktop_overlay_repro

    return verify_desktop_overlay_repro(output_dir=output_dir)


def _load_settings_or_default(
    path: Path,
    *,
    allow_stable_settings_import: bool = False,
) -> AppSettingsVNext:
    from dataclasses import replace

    from puripuly_heart.config.profile_bootstrap import import_stable_settings_if_missing
    from puripuly_heart.config.settings import detect_system_locale, resolve_first_run_ui_locale
    from puripuly_heart.config.settings_vnext.facade import load_vnext_settings
    from puripuly_heart.config.settings_vnext.schema import (
        AppSettingsVNext,
        TranslationFallbackIntent,
    )

    if path.exists():
        result = load_vnext_settings(path)
        if result.settings is None:
            raise RuntimeError(
                result.error.message if result.error is not None else str(result.status)
            )
        return result.settings

    if allow_stable_settings_import:
        import_result = import_stable_settings_if_missing(path)
        _raise_stable_settings_import_error(import_result)
        if import_result.imported and import_result.settings is not None:
            _copy_stable_secrets_after_settings_import(import_result)
            return import_result.settings

    settings = AppSettingsVNext()
    system_locale = detect_system_locale()
    locale_value = resolve_first_run_ui_locale(system_locale)
    fallback_alias = (
        "openrouter_gemma4_26b_a4b" if locale_value == "zh-CN" else "openrouter_deepseek_v4_flash"
    )
    translation = replace(
        settings.intent.translation,
        fallback=TranslationFallbackIntent(selection_alias=fallback_alias),
    )
    if locale_value == "zh-CN":
        translation = replace(
            translation,
            model="deepseek_v4_flash",
            connection="managed_china",
            openrouter_model="deepseek/deepseek-v4-flash",
            openrouter_selection_alias="deepseek_v4_flash_managed",
            openrouter_provider_routing="deepseek_only",
        )
    settings = replace(settings, intent=replace(settings.intent, translation=translation))
    if locale_value:
        settings = replace(
            settings,
            intent=replace(settings.intent, ui=replace(settings.intent.ui, locale=locale_value)),
        )

    if not settings.intent.prompts.system_prompt:
        from puripuly_heart.config.prompts import load_prompt_for_provider
        from puripuly_heart.config.settings import LLMProviderName

        default_prompt = load_prompt_for_provider(LLMProviderName.GEMINI.value)
        settings = replace(
            settings,
            intent=replace(
                settings.intent,
                prompts=replace(settings.intent.prompts, system_prompt=default_prompt),
            ),
        )
    return settings


def _copy_stable_secrets_after_settings_import(import_result: object) -> None:
    settings = getattr(import_result, "settings", None)
    source_path = getattr(import_result, "source_path", None)
    target_path = getattr(import_result, "target_path", None)
    if settings is None or source_path is None or target_path is None:
        return
    try:
        from puripuly_heart.app.wiring_secrets_factory import (
            copy_stable_secrets_to_vnext_namespace,
        )

        copy_stable_secrets_to_vnext_namespace(
            (getattr(import_result, "source_settings", None) or settings).intent.secrets,
            stable_config_path=source_path,
            vnext_config_path=target_path,
            vnext_settings=settings.intent.secrets,
        )
    except Exception:
        return


def _raise_stable_settings_import_error(import_result: object) -> None:
    error = getattr(import_result, "error", None)
    if error is None:
        return
    message = getattr(error, "message", str(error))
    raise RuntimeError(f"failed to import stable settings into vNext profile: {message}")


def _settings_config_path(args: argparse.Namespace) -> tuple[Path, bool]:
    if hasattr(args, "config"):
        return args.config, True
    return default_settings_path(), False


def _call_load_settings_or_default(
    path: Path,
    *,
    allow_stable_settings_import: bool,
) -> AppSettingsVNext:
    parameters = inspect.signature(_load_settings_or_default).parameters
    if "allow_stable_settings_import" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    ):
        return _load_settings_or_default(
            path,
            allow_stable_settings_import=allow_stable_settings_import,
        )
    return _load_settings_or_default(path)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run-desktop-overlay-repro":
        return _run_desktop_overlay_repro(
            cycles=args.cycles,
            dwell_ms=args.dwell_ms,
            output_dir=args.output_dir,
        )
    if args.command == "verify-desktop-overlay-repro":
        return _verify_desktop_overlay_repro(output_dir=args.output_dir)

    logging_sinks = configure_main_logging()
    try:
        settings_config_path, explicit_settings_config = _settings_config_path(args)
        if args.command != "run-desktop-overlay":
            args.config = settings_config_path

        if args.version:
            from puripuly_heart import __version__

            print(__version__)
            return 0

        if args.command == "run-desktop-overlay":
            return _run_desktop_overlay(args.config)

        if args.command == "run-desktop-overlay-preview":
            return _run_desktop_overlay_preview()

        if args.command == "run-gui":
            return _run_gui(
                args.config,
                debug_ui_preview=bool(getattr(args, "debug_ui_preview", False)),
                allow_stable_settings_import=not explicit_settings_config,
            )

        if args.command == "local-qwen-runtime-check":
            return run_local_qwen_runtime_check()

        if args.command == "soxr-runtime-check":
            return run_soxr_runtime_check()

        # Default: run GUI when no command specified (e.g., double-clicking EXE)
        if args.command is None:
            return _run_gui(
                args.config,
                debug_ui_preview=bool(getattr(args, "debug_ui_preview", False)),
                allow_stable_settings_import=not explicit_settings_config,
            )

        parser.print_help()
        return 2
    finally:
        logging_sinks.close(force=True)


if __name__ == "__main__":
    raise SystemExit(main())
