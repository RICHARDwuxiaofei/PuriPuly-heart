from __future__ import annotations

import pytest

from puripuly_heart.app.services.application_construction import ApplicationConstructionScope


class Resource:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self.events.append(f"close:{self.name}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_stage", "constructed"),
    [
        ("overlay", ()),
        ("runtime", ("overlay",)),
        ("application_adapters", ("overlay", "runtime")),
        ("owner", ("overlay", "runtime", "application_adapters")),
        ("ui", ("overlay", "runtime", "application_adapters")),
        ("complete_startup", ("overlay", "runtime", "application_adapters")),
    ],
)
async def test_construction_scope_closes_every_earlier_stage_exactly_once(
    failure_stage: str,
    constructed: tuple[str, ...],
) -> None:
    events: list[str] = []
    resources: dict[str, Resource] = {}
    scope = ApplicationConstructionScope()

    def construct(name: str) -> Resource:
        if failure_stage == name:
            raise RuntimeError(name)
        resource = Resource(name, events)
        resources[name] = resource
        return resource

    with pytest.raises(RuntimeError, match=failure_stage):
        for name in ("overlay", "runtime", "application_adapters"):
            scope.construct(name, lambda name=name: construct(name), close_name="close")
        raise RuntimeError(failure_stage)

    await scope.close()
    await scope.close()

    assert tuple(resources) == constructed
    assert all(resource.close_calls == 1 for resource in resources.values())
    expected_order = [
        f"close:{name}"
        for name in ("application_adapters", "overlay", "runtime")
        if name in constructed
    ]
    assert events == expected_order


@pytest.mark.asyncio
async def test_released_resource_transfers_exactly_once_and_is_not_scope_closed() -> None:
    events: list[str] = []
    scope = ApplicationConstructionScope()
    resource = scope.construct("runtime", lambda: Resource("runtime", events), close_name="close")

    assert scope.release("runtime") is resource
    await scope.close()

    assert resource.close_calls == 0
