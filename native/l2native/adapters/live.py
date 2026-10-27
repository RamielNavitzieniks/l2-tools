"""LiveAdapter: reservado para integracao futura com o cliente Lineage II Live.

NAO IMPLEMENTADO. Nao existe hoje mecanismo de leitura de estado ou envio de
comandos tecnicamente comprovado e autorizado para o Live NA/Kain. Este modulo
nao contem (e nao deve conter) opcodes, offsets, estruturas internas ou
metodos do cliente presumidos. Ver docs/pesquisa/integracao-live-020.md.
"""
from __future__ import annotations

from ..actions import ActionKind, ActionResult, Intent, ResultStatus
from ..model import GameState
from .base import AdapterUnavailable

REASON = ("LiveAdapter nao implementado: nenhum mecanismo de integracao com o "
          "Lineage II Live foi comprovado e autorizado")


class LiveAdapter:
    name = "live"

    def observe(self) -> GameState:
        raise AdapterUnavailable(REASON)

    def observes(self) -> frozenset[str]:
        return frozenset()

    def capabilities(self) -> frozenset[ActionKind]:
        return frozenset()

    def execute(self, intent: Intent) -> ActionResult:
        return ActionResult(intent, ResultStatus.UNSUPPORTED, REASON)

    def close(self) -> None:
        pass
