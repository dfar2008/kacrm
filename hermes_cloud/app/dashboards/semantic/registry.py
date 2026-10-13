"""Registry — load all entity YAMLs from a directory and look them up by name."""
from __future__ import annotations

import pathlib
from typing import Iterable, List

from .loader import load_entity_yaml
from .model import Entity


class Registry:
    """Holds N entities. Built once at process start, treated as immutable."""

    def __init__(self, entities: Iterable[Entity]):
        self._by_name: dict[str, Entity] = {}
        for e in entities:
            if e.name in self._by_name:
                raise ValueError(f"duplicate entity name: {e.name!r}")
            self._by_name[e.name] = e

    @classmethod
    def from_directory(cls, dir_path: pathlib.Path | str) -> "Registry":
        """Load every ``*.yml`` file under ``dir_path`` (non-recursive)."""
        d = pathlib.Path(dir_path)
        if not d.is_dir():
            raise FileNotFoundError(f"{d!r} is not a directory")
        entities = [load_entity_yaml(p) for p in sorted(d.glob("*.yml"))]
        return cls(entities)

    def entity(self, name: str) -> Entity:
        if name not in self._by_name:
            raise KeyError(f"entity {name!r} not registered")
        return self._by_name[name]

    def has_entity(self, name: str) -> bool:
        return name in self._by_name

    def entity_names(self) -> List[str]:
        return list(self._by_name.keys())

    def __len__(self) -> int:
        return len(self._by_name)
