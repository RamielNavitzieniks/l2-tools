"""Catalogo de entidades vistas na sessao, separado do snapshot (registro 022).

O GameState descreve apenas o ciclo atual. O catalogo acumula o que ja foi
observado (primeira/ultima vez, numero de avistamentos, ultima posicao), para
a GUI e futuras politicas. Fica so em memoria: nomes de terceiros nao sao
gravados em disco.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from .model import Entity, GameState, Position

Key = tuple[str, Union[int, str]]


@dataclass
class CatalogEntry:
    kind: str                 # "npc" ou "character"
    name: Optional[str]
    npc_id: Optional[int]
    first_tick: int
    last_tick: int
    sightings: int
    last_position: Optional[Position]


class EntityCatalog:
    def __init__(self) -> None:
        self.entries: dict[Key, CatalogEntry] = {}

    @staticmethod
    def key(kind: str, entity: Entity) -> Optional[Key]:
        if kind == "npc" and entity.npc_id is not None:
            return ("npc", entity.npc_id)
        if entity.name:
            return (kind, entity.name.casefold())
        return None  # sem identidade estavel: nao entra no catalogo

    def update(self, state: GameState) -> list[CatalogEntry]:
        """Incorpora as listas CONHECIDAS do estado; devolve as entradas novas."""
        new = []
        for kind, fact in (("character", state.characters), ("npc", state.npcs)):
            if not fact.known:
                continue
            for entity in fact.value:
                key = self.key(kind, entity)
                if key is None:
                    continue
                pos = entity.position.value if entity.position.known else None
                entry = self.entries.get(key)
                if entry is None:
                    entry = CatalogEntry(kind, entity.name, entity.npc_id, state.tick, state.tick, 0, pos)
                    self.entries[key] = entry
                    new.append(entry)
                if entry.last_tick != state.tick or entry.sightings == 0:
                    entry.sightings += 1
                entry.last_tick = state.tick
                entry.name = entity.name or entry.name
                if pos is not None:
                    entry.last_position = pos
        return new

    def clear(self) -> None:
        self.entries.clear()
