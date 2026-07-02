from __future__ import annotations

import argparse
import asyncio
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any

from puripuly_heart.config.paths import default_settings_path, default_vad_model_path
from puripuly_heart.core.runtime_logging import configure_main_logging

if TYPE_CHECKING:
    from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext

HeadlessStdinRunner: Any | None = None
VrchatOscUdpSender: Any | None = None
SoxrRuntimeAvailabilityError: type[Exception] | None = None


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

    osc_send = sub.add_parser("osc-send", help="Send a single VRChat chatbox OSC message")
    osc_send.add_argument("text", help="Text to send")

    stdin = sub.add_parser("run-stdin", help="Read lines from stdin and send to OSC")
    stdin.add_argument(
        "--use-llm",
        action="store_true",
        help="Translate each line using configured LLM provider (requires provider setup)",
    )

    mic = sub.add_parser("run-mic", help="Capture microphone audio (VAD→STT→LLM→OSC)")
    mic.add_argument(
        "--vad-model",
        type=Path,
        default=default_vad_model_path(),
        help="Path to Silero VAD ONNX model file (default: user config dir)",
    )
    mic.add_argument(
        "--use-llm",
        action="store_true",
        help="Translate STT final results using configured LLM provider",
    )

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


def _print_initialization_error(component: str, exc: Exception) -> int:
    print(f"Error: failed to initialize {component}: {exc}", flush=True)
    return 2


def _print_runtime_error(component: str, exc: Exception) -> int:
    print(f"Error: failed to verify {component}: {exc}", flush=True)
    return 2


def _load_headless_mic_types():
    from puripuly_heart.app.headless_mic import HeadlessMicInitializationError, HeadlessMicRunner

    return HeadlessMicRunner, HeadlessMicInitializationError


def _load_headless_stdin_runner():
    global HeadlessStdinRunner
    if HeadlessStdinRunner is None:
        from puripuly_heart.app.headless_stdin import HeadlessStdinRunner as LoadedRunner

        HeadlessStdinRunner = LoadedRunner
    return HeadlessStdinRunner


def _load_vrchat_osc_udp_sender():
    global VrchatOscUdpSender
    if VrchatOscUdpSender is None:
        from puripuly_heart.core.osc.udp_sender import VrchatOscUdpSender as LoadedSender

        VrchatOscUdpSender = LoadedSender
    return VrchatOscUdpSender


def _soxr_runtime_availability_error_type() -> type[Exception]:
    global SoxrRuntimeAvailabilityError
    if SoxrRuntimeAvailabilityError is None:
        from puripuly_heart.core.soxr_runtime import (
            SoxrRuntimeAvailabilityError as LoadedError,
        )

        SoxrRuntimeAvailabilityError = LoadedError
    return SoxrRuntimeAvailabilityError


def ensure_soxr_runtime_available_for_startup():
    from puripuly_heart.core.soxr_runtime import ensure_soxr_runtime_available_for_startup as run

    return run()


def run_local_qwen_runtime_check() -> int:
    from puripuly_heart.app.local_qwen_runtime_check import run_local_qwen_runtime_check as run

    return run()


def run_soxr_runtime_check() -> int:
    from puripuly_heart.app.soxr_runtime_check import run_soxr_runtime_check as run

    return run()


def _requires_soxr_runtime_startup_check(args: argparse.Namespace) -> bool:
    return args.command == "run-mic"


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


def _load_settings_or_default(
    path: Path,
    *,
    allow_stable_settings_import: bool = False,
) -> AppSettingsVNext:
    from dataclasses import replace

    from puripuly_heart.config.profile_bootstrap import import_stable_settings_if_missing
    from puripuly_heart.config.settings import (
        detect_system_locale,
        resolve_first_run_ui_locale,
    )
    from puripuly_heart.config.settings_vnext.facade import load_vnext_settings
    from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext

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
    locale_value = resolve_first_run_ui_locale(detect_system_locale())
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
    logging_sinks = configure_main_logging()
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        settings_config_path, explicit_settings_config = _settings_config_path(args)
        if args.command != "run-desktop-overlay":
            args.config = settings_config_path

        if args.version:
            from puripuly_heart import __version__

            print(__version__)
            return 0

        try:
            if _requires_soxr_runtime_startup_check(args):
                ensure_soxr_runtime_available_for_startup()
        except _soxr_runtime_availability_error_type() as exc:
            return _print_runtime_error("packaged soxr runtime", exc)

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

        settings = _call_load_settings_or_default(
            args.config,
            allow_stable_settings_import=not explicit_settings_config,
        )

        if args.command == "osc-send":
            sender_cls = _load_vrchat_osc_udp_sender()
            sender = sender_cls(
                host=settings.intent.osc.host,
                port=settings.intent.osc.port,
                chatbox_address=settings.intent.osc.chatbox_address,
                chatbox_send=settings.intent.osc.chatbox_send,
                chatbox_clear=settings.intent.osc.chatbox_clear,
            )
            try:
                sender.send_chatbox(args.text)
            finally:
                sender.close()
            return 0

        if args.command == "run-stdin":
            from puripuly_heart.app.headless_runtime_config import (
                build_headless_stdin_runtime_config,
                create_secret_store_from_vnext_intent,
                resolve_llm_config_from_vnext_settings,
            )

            llm = None
            if args.use_llm:
                from puripuly_heart.app.wiring import create_llm_provider_from_resolved_config

                try:
                    secrets = create_secret_store_from_vnext_intent(
                        settings, config_path=args.config
                    )
                    llm_config = resolve_llm_config_from_vnext_settings(settings)
                    llm = create_llm_provider_from_resolved_config(
                        llm_config,
                        secrets=secrets,
                        compatibility_settings=None,
                        qwen_low_latency_mode=settings.intent.stt.low_latency_mode,
                    )
                except Exception as exc:
                    return _print_initialization_error("LLM provider", exc)

            runner_cls = _load_headless_stdin_runner()
            runtime_config = build_headless_stdin_runtime_config(settings)
            runner = runner_cls(runtime_config=runtime_config, llm=llm)
            return asyncio.run(runner.run())

        if args.command == "run-mic":
            from puripuly_heart.app.headless_runtime_config import (
                build_headless_mic_runtime_config,
                create_secret_store_from_vnext_intent,
            )

            HeadlessMicRunner, HeadlessMicInitializationError = _load_headless_mic_types()
            try:
                secrets = create_secret_store_from_vnext_intent(settings, config_path=args.config)
                runtime_config = build_headless_mic_runtime_config(
                    settings,
                    config_path=args.config,
                    vad_model_path=args.vad_model,
                    use_llm=args.use_llm,
                    secret_store=secrets,
                )
            except Exception as exc:
                return _print_initialization_error("headless mic runner", exc)

            runner = HeadlessMicRunner(runtime_config=runtime_config)
            try:
                return asyncio.run(runner.run())
            except HeadlessMicInitializationError as exc:
                return _print_initialization_error("headless mic runner", exc)

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
