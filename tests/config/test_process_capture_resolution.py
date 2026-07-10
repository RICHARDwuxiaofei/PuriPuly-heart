from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from puripuly_heart.config.process_capture_platform import ProcessCapturePlatformAvailability
from puripuly_heart.config.process_capture_resolution import (
    ProcessCaptureResolver,
    ProcessSnapshot,
)
from puripuly_heart.config.settings_vnext.schema import ProcessCaptureTargetIntent


@dataclass
class SnapshotPort:
    batches: list[tuple[ProcessSnapshot, ...]]
    calls: int = field(init=False, default=0)

    def snapshots(self) -> tuple[ProcessSnapshot, ...]:
        index = min(self.calls, len(self.batches) - 1)
        self.calls += 1
        return self.batches[index]


def _supported() -> ProcessCapturePlatformAvailability:
    return ProcessCapturePlatformAvailability(available=True)


def _snapshot(
    pid: int,
    path: str | None,
    *,
    parent_pid: int | None = None,
    current_user: bool = True,
) -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=pid,
        parent_pid=parent_pid,
        is_current_user=current_user,
        executable_path=path,
    )


def test_candidate_enumeration_filters_and_orders_supported_targets() -> None:
    snapshots = SnapshotPort(
        [
            (
                _snapshot(1, r"C:\Apps\Zeta\Zeta.exe"),
                _snapshot(2, r"C:\Apps\Alpha\Alpha.exe"),
                _snapshot(3, r"C:\VRChat\VRChat.exe"),
                _snapshot(4, r"C:\Discord\DiscordCanary.exe"),
                _snapshot(5, r"C:\Discord\Discord.exe"),
                _snapshot(6, r"C:\Discord\DiscordPTB.exe"),
                _snapshot(7, None),
                _snapshot(8, r"C:\Apps\Other\Other.exe", current_user=False),
                _snapshot(9, r"C:\Steam\Steam.exe"),
                _snapshot(10, r"C:\Apps\launch.exe"),
                _snapshot(11, r"C:\Apps\Updater.exe"),
                _snapshot(12, r"C:\Apps\Setup.exe"),
                _snapshot(13, r"C:\Discord\DiscordDevelopment.exe"),
                _snapshot(14, r"C:\Discord\DiscordInternal.exe"),
                _snapshot(15, r"C:\Apps\GameLauncher.exe"),
                _snapshot(16, r"C:\Apps\Launcher.exe"),
                _snapshot(17, r"C:\Apps\install.exe"),
            )
        ]
    )
    resolver = ProcessCaptureResolver(snapshots=snapshots, platform_availability=_supported)

    candidates = resolver.enumerate_candidates()

    assert [(candidate.name, candidate.enabled) for candidate in candidates] == [
        ("VRChat", True),
        ("Discord Stable", True),
        ("Discord PTB", True),
        ("Discord Canary", True),
        ("Alpha", True),
        ("Zeta", True),
    ]
    assert [candidate.target.kind for candidate in candidates] == [
        "vrchat",
        "discord",
        "discord",
        "discord",
        "generic_executable",
        "generic_executable",
    ]
    assert not hasattr(candidates[0], "pid")


def test_same_identity_ancestor_suppresses_descendant_across_ineligible_intermediary() -> None:
    snapshots = SnapshotPort(
        [
            (
                _snapshot(10, r"C:\VRChat\VRChat.exe"),
                _snapshot(11, r"C:\Steam\launch.exe", parent_pid=10),
                _snapshot(12, r"C:\VRChat\VRChat.exe", parent_pid=11),
            )
        ]
    )
    resolver = ProcessCaptureResolver(snapshots=snapshots, platform_availability=_supported)

    candidates = resolver.enumerate_candidates()

    assert [(candidate.name, candidate.enabled) for candidate in candidates] == [("VRChat", True)]


def test_generic_candidates_with_colliding_names_use_identity_tie_breaker_and_exact_matching() -> (
    None
):
    alpha = _snapshot(60, r"C:\Apps\Alpha\Game.exe")
    beta = _snapshot(61, r"C:\Apps\Beta\Game.exe")
    target_alpha = ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\Alpha\Game.exe")
    target_beta = ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\Beta\Game.exe")
    expected_identities = [
        r"c:\apps\alpha\game.exe",
        r"c:\apps\beta\game.exe",
    ]

    first = ProcessCaptureResolver(
        snapshots=SnapshotPort([(beta, alpha)]),
        platform_availability=_supported,
    )
    second = ProcessCaptureResolver(
        snapshots=SnapshotPort([(alpha, beta)]),
        platform_availability=_supported,
    )

    assert [candidate.name for candidate in first.enumerate_candidates()] == ["Game", "Game"]
    assert [candidate.target.executable_identity for candidate in first.enumerate_candidates()] == (
        expected_identities
    )
    assert [
        candidate.target.executable_identity for candidate in second.enumerate_candidates()
    ] == (expected_identities)
    assert first.resolve_for_start(target_alpha).pid == 60
    assert first.resolve_for_start(target_beta).pid == 61
    assert (
        first.resolve_for_start(
            ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\Other\Game.exe")
        ).unavailable_reason
        == "no_process"
    )


def test_ambiguous_roots_disable_one_name_only_candidate_and_do_not_choose_a_pid() -> None:
    snapshots = SnapshotPort(
        [
            (
                _snapshot(20, r"C:\VRChat\VRChat.exe"),
                _snapshot(21, r"C:\VRChat\VRChat.exe"),
            )
        ]
    )
    resolver = ProcessCaptureResolver(snapshots=snapshots, platform_availability=_supported)
    target = ProcessCaptureTargetIntent.vrchat(r"C:\VRChat\VRChat.exe")

    candidates = resolver.enumerate_candidates()
    resolution = resolver.resolve_for_start(target)

    assert [(candidate.name, candidate.enabled) for candidate in candidates] == [
        ("VRChat (2)", False)
    ]
    assert resolution.pid is None
    assert resolution.unavailable_reason == "ambiguous"


@pytest.mark.parametrize(
    ("snapshots", "reason"),
    [
        ((), "no_process"),
        ((_snapshot(31, r"C:\Apps\Game\Game.exe", current_user=False),), "ineligible"),
    ],
)
def test_resolution_returns_typed_safe_unavailable_reasons(
    snapshots: tuple[ProcessSnapshot, ...],
    reason: str,
) -> None:
    resolver = ProcessCaptureResolver(
        snapshots=SnapshotPort([snapshots]),
        platform_availability=_supported,
    )
    target = ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\Game\Game.exe")

    resolution = resolver.resolve_for_start(target)

    assert resolution.pid is None
    assert resolution.unavailable_reason == reason
    assert "\\" not in reason


def test_start_and_retry_resolve_a_fresh_pid_without_retaining_prior_resolution() -> None:
    snapshots = SnapshotPort(
        [
            (_snapshot(40, r"C:\Apps\Game\Game.exe"),),
            (_snapshot(41, r"C:\Apps\Game\Game.exe"),),
        ]
    )
    resolver = ProcessCaptureResolver(snapshots=snapshots, platform_availability=_supported)
    target = ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\Game\Game.exe")

    first = resolver.resolve_for_start(target)
    retry = resolver.resolve_for_retry(target)

    assert first.pid == 40
    assert retry.pid == 41
    assert snapshots.calls == 2


def test_unsupported_platform_is_unavailable_without_process_inspection() -> None:
    snapshots = SnapshotPort([(_snapshot(50, r"C:\Apps\Game\Game.exe"),)])
    resolver = ProcessCaptureResolver(
        snapshots=snapshots,
        platform_availability=lambda: ProcessCapturePlatformAvailability(
            available=False,
            reason="unsupported_windows_build",
        ),
    )
    target = ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\Game\Game.exe")

    assert resolver.enumerate_candidates() == ()
    resolution = resolver.resolve_for_start(target)
    assert resolution.pid is None
    assert resolution.unavailable_reason == "unsupported_platform"
    assert snapshots.calls == 0
