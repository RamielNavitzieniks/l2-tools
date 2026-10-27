"""Contrato GameAdapter: unica fronteira entre o motor e uma fonte de jogo."""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from ..actions import ActionKind, ActionResult, Intent
from ..model import GameState


class AdapterError(RuntimeError):
    """Falha recuperavel ao observar (captura indisponivel, linha corrompida...)."""


class SourceExhausted(Exception):
    """A fonte terminou normalmente (fim do replay ou do cenario simulado)."""


class AdapterUnavailable(RuntimeError):
    """O adaptador nao existe ou nao pode operar neste ambiente. Nao recuperavel."""


@runtime_checkable
class GameAdapter(Protocol):
    name: str

    def observe(self) -> GameState:
        """Devolve o estado atual. Pode levantar AdapterError, SourceExhausted
        ou AdapterUnavailable."""
        ...

    def observes(self) -> Optional[frozenset[str]]:
        """Campos do GameState que este adaptador se compromete a observar
        (capacidades de observacao). None = nao declara (depende dos dados)."""
        ...

    def capabilities(self) -> frozenset[ActionKind]:
        """Acoes com backend implementado neste adaptador. O motor nao envia
        ao adaptador nenhuma acao fora deste conjunto."""
        ...

    def execute(self, intent: Intent) -> ActionResult:
        """Executa uma intencao ja validada e contida em capabilities()."""
        ...

    def close(self) -> None:
        ...
