from __future__ import annotations

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.managed_trial_usage_bar import ManagedTrialUsageBar
from puripuly_heart.ui.i18n import t


def test_managed_trial_usage_bar_renders_placeholder_when_percent_unknown() -> None:
    bar = ManagedTrialUsageBar()

    assert bar.percent is None
    assert bar._remaining_text.value == t("settings.managed_trial_usage.remaining_placeholder")
    assert bar._fill.height == 0


@pytest.mark.parametrize(
    ("percent", "expected_percent"),
    [
        (42, 42),
        (-5, 0),
        (135, 100),
    ],
)
def test_managed_trial_usage_bar_formats_and_clamps_percent(
    percent: int,
    expected_percent: int,
) -> None:
    bar = ManagedTrialUsageBar(percent=percent)

    assert bar.percent == expected_percent
    assert bar._remaining_text.value == t(
        "settings.managed_trial_usage.remaining",
        percent=expected_percent,
    )
    assert bar._fill.height == bar._fill_height_for_percent(expected_percent)
