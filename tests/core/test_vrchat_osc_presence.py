from __future__ import annotations

from types import SimpleNamespace

from puripuly_heart.core.osc.vrchat_osc_presence import probe_vrchat_osc_presence


def test_probe_vrchat_osc_presence_not_running() -> None:
    presence = probe_vrchat_osc_presence(
        port=9000,
        process_iter=lambda *_args, **_kwargs: [],
        net_connections=lambda *_args, **_kwargs: [],
    )
    assert presence.vrchat_running is False
    assert presence.osc_listening is None
    assert presence.should_prompt_enable_osc is False


def test_probe_vrchat_osc_presence_running_without_listener() -> None:
    processes = [SimpleNamespace(info={"pid": 11, "name": "VRChat.exe"})]
    presence = probe_vrchat_osc_presence(
        port=9000,
        process_iter=lambda *_args, **_kwargs: processes,
        net_connections=lambda *_args, **_kwargs: [],
    )
    assert presence.vrchat_running is True
    assert presence.osc_listening is False
    assert presence.should_prompt_enable_osc is True


def test_probe_vrchat_osc_presence_running_with_listener() -> None:
    processes = [SimpleNamespace(info={"pid": 22, "name": "VRChat.exe"})]
    connections = [SimpleNamespace(laddr=SimpleNamespace(port=9000), pid=22)]
    presence = probe_vrchat_osc_presence(
        port=9000,
        process_iter=lambda *_args, **_kwargs: processes,
        net_connections=lambda *_args, **_kwargs: connections,
    )
    assert presence.vrchat_running is True
    assert presence.osc_listening is True
    assert presence.should_prompt_enable_osc is False
