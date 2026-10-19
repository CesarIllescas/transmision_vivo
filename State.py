"""Named state registry retained for views that share application data."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

T = TypeVar("T")


class GlobalState:
    def __init__(self) -> None:
        self._state: dict[str, State[Any]] = {}

    def register_state(self, state: State[Any]) -> None:
        self._state[state.get_key()] = state

    def get_state_by_key(self, key: str) -> State[Any] | None:
        return self._state.get(key)


global_state = GlobalState()


class State(Generic[T]):
    def __init__(
        self,
        key: str,
        value: T | None = None,
        *,
        registry: GlobalState | None = None,
    ) -> None:
        self._global_state = registry if registry is not None else global_state
        self._key = key
        self._value = value
        self.register_with_global()

    def register_with_global(self) -> None:
        self._global_state.register_state(self)

    def get_key(self) -> str:
        return self._key

    def get_state(self) -> T | None:
        return self._value
