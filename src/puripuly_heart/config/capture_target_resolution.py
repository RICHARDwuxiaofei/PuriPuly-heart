from __future__ import annotations

from puripuly_heart.config.resolved import ResolvedDesktopAudioCaptureTarget
from puripuly_heart.config.settings_vnext.schema import CaptureTargetIntent


def resolve_desktop_audio_capture_target(
    capture_target: CaptureTargetIntent,
) -> ResolvedDesktopAudioCaptureTarget:
    if capture_target.kind == "default_output_device":
        return ResolvedDesktopAudioCaptureTarget(kind="default_output_device")
    if capture_target.kind == "named_output_device":
        return ResolvedDesktopAudioCaptureTarget(
            kind="named_output_device",
            device_name=capture_target.device_name,
        )
    process = capture_target.process
    if process is None:
        raise ValueError("process capture target requires a process identity")
    return ResolvedDesktopAudioCaptureTarget(
        kind="process",
        process_kind=process.kind,
        executable_identity=process.executable_identity,
        discord_channel=process.discord_channel,
        executable_basename=process.executable_basename,
    )
