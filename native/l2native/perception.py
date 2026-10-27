"""Consultas de leitura sobre o GameState, para scripts.

Metodos booleanos so devolvem True quando o valor e conhecido; use os Facts
(`perception.state.<campo>`) quando o script precisar distinguir "falso" de
"desconhecido".
"""
from __future__ import annotations

from typing import Optional

from .model import Entity, Fact, GameState, Position


class Perception:
    def __init__(self, state: GameState) -> None:
        self.state = state

    def quest_dialog_visible(self) -> bool:
        return self.state.quest_dialog.is_true()

    def quest_dialog_unknown(self) -> bool:
        return not self.state.quest_dialog.known

    def position(self) -> Optional[Position]:
        fact = self.state.self_position
        return fact.value if fact.known else None

    def characters_known(self) -> bool:
        return self.state.characters.known

    def character(self, name: str) -> Optional[Entity]:
        """Personagem visivel pelo nome (sem diferenciar caixa). None se ausente
        OU se a lista e desconhecida: consulte characters_known()."""
        if not self.state.characters.known:
            return None
        wanted = name.casefold()
        for entity in self.state.characters.value:
            if entity.name is not None and entity.name.casefold() == wanted:
                return entity
        return None

    def fact(self, name: str) -> Fact:
        if name not in GameState.FACT_FIELDS:
            raise KeyError(f"campo desconhecido: {name}")
        return getattr(self.state, name)
