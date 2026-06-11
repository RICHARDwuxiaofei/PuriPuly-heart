from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "puripuly_heart"

ASYNCIO_CREATE_TASK = "asyncio.create_task"
ASYNCIO_ENSURE_FUTURE = "asyncio.ensure_future"
RUN_TASK = ".run_task"

LIFECYCLE_OWNER_PRIMITIVES = frozenset(
    {
        "src/puripuly_heart/core/lifecycle.py",
    }
)

LEGACY_TASK_CREATION_ALLOWLIST = Counter(
    {
        ("src/puripuly_heart/app/headless_stdin.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/llm/fallback_racing.py", ASYNCIO_CREATE_TASK): 1,
        (
            "src/puripuly_heart/core/local_stt_runtime_installer.py",
            ASYNCIO_CREATE_TASK,
        ): 1,
        ("src/puripuly_heart/core/stt/controller.py", ASYNCIO_CREATE_TASK): 6,
        ("src/puripuly_heart/core/orchestrator/hub.py", ASYNCIO_CREATE_TASK): 5,
        ("src/puripuly_heart/core/overlay/bridge.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/overlay/presenter.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/overlay/process.py", ASYNCIO_CREATE_TASK): 2,
        ("src/puripuly_heart/core/overlay/sink.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/providers/stt/soniox.py", ASYNCIO_CREATE_TASK): 3,
        ("src/puripuly_heart/ui/app.py", RUN_TASK): 13,
        ("src/puripuly_heart/ui/components/settings/api_key_field.py", RUN_TASK): 1,
        ("src/puripuly_heart/ui/controller.py", ASYNCIO_CREATE_TASK): 3,
        ("src/puripuly_heart/ui/desktop_overlay.py", ASYNCIO_CREATE_TASK): 11,
    }
)

NAMED_LIFECYCLE_OWNER_TASK_ALLOWLIST = Counter(
    {
        ("src/puripuly_heart/core/runtime/peer_channel.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/runtime/provider_handle.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/runtime/self_audio.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/runtime/overlay.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/runtime/output.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/runtime/oauth.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/runtime/clipboard.py", ASYNCIO_CREATE_TASK): 1,
        (
            "src/puripuly_heart/core/runtime/github_star_prompt.py",
            ASYNCIO_CREATE_TASK,
        ): 2,
        ("src/puripuly_heart/core/runtime/local_stt_download.py", ASYNCIO_CREATE_TASK): 1,
        ("src/puripuly_heart/core/runtime/mic_test.py", ASYNCIO_CREATE_TASK): 2,
        ("src/puripuly_heart/core/runtime/receiver.py", ASYNCIO_CREATE_TASK): 1,
    }
)


def _repo_path(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _is_asyncio_create_task_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_task"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "asyncio"
    )


def _is_asyncio_ensure_future_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "ensure_future"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "asyncio"
    )


def _is_run_task_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == "run_task"


def _task_creation_counts() -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for source_file in sorted(SOURCE_ROOT.rglob("*.py")):
        relative_path = _repo_path(source_file)
        if relative_path in LIFECYCLE_OWNER_PRIMITIVES:
            continue

        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _is_asyncio_create_task_call(node):
                counts[(relative_path, ASYNCIO_CREATE_TASK)] += 1
            elif _is_asyncio_ensure_future_call(node):
                counts[(relative_path, ASYNCIO_ENSURE_FUTURE)] += 1
            elif _is_run_task_call(node):
                counts[(relative_path, RUN_TASK)] += 1
    return counts


def test_lifecycle_scope_file_is_the_allowed_task_owner_primitive() -> None:
    assert (REPO_ROOT / "src" / "puripuly_heart" / "core" / "lifecycle.py").is_file()


def test_no_new_unmanaged_task_creation_outside_lifecycle_allowlist() -> None:
    actual = _task_creation_counts()
    expected = LEGACY_TASK_CREATION_ALLOWLIST + NAMED_LIFECYCLE_OWNER_TASK_ALLOWLIST
    unexpected = actual - expected
    stale = expected - actual

    assert not unexpected and not stale, (
        "Unmanaged background task inventory changed. New async work must go "
        "through LifecycleScope or a named lifecycle owner method; legacy "
        "exceptions must be reviewed before updating this allowlist.\n"
        f"Unexpected occurrences: {dict(unexpected)}\n"
        f"Stale allowlist entries: {dict(stale)}"
    )


def test_order34_named_owner_allowlist_does_not_claim_stt_controller_legacy_tasks() -> None:
    stt_controller_tasks = (
        "src/puripuly_heart/core/stt/controller.py",
        ASYNCIO_CREATE_TASK,
    )

    assert stt_controller_tasks in LEGACY_TASK_CREATION_ALLOWLIST
    assert stt_controller_tasks not in NAMED_LIFECYCLE_OWNER_TASK_ALLOWLIST


def test_order37_named_owner_allowlist_preserves_remaining_legacy_ui_task_debt() -> None:
    assert (
        "src/puripuly_heart/ui/controller.py",
        ASYNCIO_CREATE_TASK,
    ) in LEGACY_TASK_CREATION_ALLOWLIST
    assert ("src/puripuly_heart/ui/app.py", RUN_TASK) in LEGACY_TASK_CREATION_ALLOWLIST
    assert (
        "src/puripuly_heart/core/managed_openrouter_release.py",
        ASYNCIO_CREATE_TASK,
    ) not in LEGACY_TASK_CREATION_ALLOWLIST
    assert (
        "src/puripuly_heart/core/runtime/oauth.py",
        ASYNCIO_CREATE_TASK,
    ) in NAMED_LIFECYCLE_OWNER_TASK_ALLOWLIST
    assert (
        "src/puripuly_heart/core/runtime/clipboard.py",
        ASYNCIO_CREATE_TASK,
    ) in NAMED_LIFECYCLE_OWNER_TASK_ALLOWLIST


def test_order38_named_owner_allowlist_preserves_installer_legacy_task_debt() -> None:
    assert (
        "src/puripuly_heart/core/local_stt_runtime_installer.py",
        ASYNCIO_CREATE_TASK,
    ) in LEGACY_TASK_CREATION_ALLOWLIST
    assert (
        "src/puripuly_heart/core/runtime/local_stt_download.py",
        ASYNCIO_CREATE_TASK,
    ) in NAMED_LIFECYCLE_OWNER_TASK_ALLOWLIST
    assert (
        "src/puripuly_heart/core/runtime/mic_test.py",
        ASYNCIO_CREATE_TASK,
    ) in NAMED_LIFECYCLE_OWNER_TASK_ALLOWLIST


def test_order39_named_owner_allowlist_adds_receiver_and_prompt_owners() -> None:
    assert (
        "src/puripuly_heart/core/runtime/receiver.py",
        ASYNCIO_CREATE_TASK,
    ) in NAMED_LIFECYCLE_OWNER_TASK_ALLOWLIST
    assert (
        "src/puripuly_heart/core/runtime/github_star_prompt.py",
        ASYNCIO_CREATE_TASK,
    ) in NAMED_LIFECYCLE_OWNER_TASK_ALLOWLIST
    assert (
        "src/puripuly_heart/ui/controller.py",
        ASYNCIO_CREATE_TASK,
    ) in LEGACY_TASK_CREATION_ALLOWLIST
    assert ("src/puripuly_heart/ui/app.py", RUN_TASK) in LEGACY_TASK_CREATION_ALLOWLIST
