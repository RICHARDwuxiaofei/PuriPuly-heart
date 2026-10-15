from dataclasses import replace

import pytest

from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.wrist_vision import WristVisionIntent


def test_old_settings_default_to_disabled_and_round_trip():
    raw = serialization.to_dict(AppSettingsVNext())
    del raw["intent"]["wrist_vision"]
    loaded = serialization.from_dict(raw)
    assert loaded.intent.wrist_vision == WristVisionIntent()
    assert not loaded.intent.wrist_vision.enabled
    updated = replace(
        loaded,
        intent=replace(loaded.intent, wrist_vision=WristVisionIntent(hand="right", enabled=True)),
    )
    assert serialization.from_dict(serialization.to_dict(updated)) == updated


@pytest.mark.parametrize(
    "values",
    [
        {"width_m": float("nan")},
        {"opacity": 0},
        {"hand": "head"},
        {"model": "http://unsafe"},
        {"enabled": "yes"},
    ],
)
def test_invalid_configuration_rejected(values):
    with pytest.raises(ValueError):
        WristVisionIntent(**values)
