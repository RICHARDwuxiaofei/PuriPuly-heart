from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Callable


@dataclass(slots=True)
class _ConstructionResource:
    name: str
    resource: object
    close_name: str


class ApplicationConstructionScope:
    def __init__(self) -> None:
        self._resources: dict[str, _ConstructionResource] = {}

    def construct(
        self,
        name: str,
        factory: Callable[[], object],
        *,
        close_name: str,
        owned_resource: Callable[[object], object] | None = None,
    ) -> object:
        result = factory()
        resource = result if owned_resource is None else owned_resource(result)
        self._resources[name] = _ConstructionResource(name, resource, close_name)
        return result

    def release(self, name: str) -> object:
        return self._resources.pop(name).resource

    async def close(self) -> None:
        failures: list[BaseException] = []
        order = ("application_adapters", "overlay", "runtime", "presentation")
        names = [name for name in order if name in self._resources]
        names.extend(name for name in reversed(self._resources) if name not in names)
        for name in names:
            owned = self._resources.get(name)
            if owned is None:
                continue
            close = getattr(owned.resource, owned.close_name)
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except BaseException as exc:
                failures.append(exc)
            else:
                self._resources.pop(name, None)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("application construction cleanup failed", failures)


__all__ = ["ApplicationConstructionScope"]
