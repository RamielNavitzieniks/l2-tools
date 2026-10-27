"""Contrato de acoes: intencoes produzidas por scripts e seus resultados.

Uma intencao NAO e uma acao executada. So o adaptador pode devolver
COMPLETED ou DISPATCHED; qualquer outro caminho termina em REJECTED,
UNSUPPORTED, INTERRUPTED ou FAILED, sempre registrado.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


class ActionKind(str, Enum):
    MOVE_TO = "move_to"
    FOLLOW = "follow"
    SELECT_TARGET = "select_target"
    ATTACK = "attack"
    USE_SKILL = "use_skill"
    TELEPORT = "teleport"
    PAUSE = "pause"   # tratada pelo motor, nunca enviada ao adaptador
    STOP = "stop"     # tratada pelo motor, nunca enviada ao adaptador


ENGINE_ACTIONS = frozenset({ActionKind.PAUSE, ActionKind.STOP})


class ResultStatus(str, Enum):
    COMPLETED = "completed"      # adaptador confirma que a acao terminou
    DISPATCHED = "dispatched"    # adaptador enviou o comando; efeito ainda nao verificado
    REJECTED = "rejected"        # parametros invalidos ou politica de seguranca recusou
    UNSUPPORTED = "unsupported"  # nao ha backend validado para esta acao neste adaptador
    INTERRUPTED = "interrupted"  # cancelada antes do envio (pausa, parada, cancelamento)
    FAILED = "failed"            # o adaptador tentou e falhou, ou devolveu algo invalido

    @property
    def succeeded(self) -> bool:
        return self in (ResultStatus.COMPLETED, ResultStatus.DISPATCHED)


@dataclass(frozen=True)
class Intent:
    id: int
    kind: ActionKind
    params: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind.value, "params": dict(self.params)}


@dataclass(frozen=True)
class ActionResult:
    intent: Intent
    status: ResultStatus
    detail: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"intent": self.intent.to_dict(), "status": self.status.value}
        if self.detail:
            out["detail"] = self.detail
        if self.data:
            out["data"] = dict(self.data)
        return out


def _number(params: Mapping[str, Any], key: str) -> Optional[str]:
    v = params.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return f"'{key}' deve ser numero finito"
    return None


def _positive_int(params: Mapping[str, Any], key: str) -> Optional[str]:
    v = params.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        return f"'{key}' deve ser inteiro positivo"
    return None


def _text(params: Mapping[str, Any], key: str) -> Optional[str]:
    v = params.get(key)
    if not isinstance(v, str) or not v.strip():
        return f"'{key}' deve ser texto nao vazio"
    return None


_SCHEMAS: dict[ActionKind, tuple[set[str], set[str]]] = {
    # tipo: (obrigatorios, opcionais)
    ActionKind.MOVE_TO: ({"x", "y", "z"}, set()),
    ActionKind.FOLLOW: ({"character", "distance"}, set()),
    ActionKind.SELECT_TARGET: (set(), {"name", "npc_id"}),
    ActionKind.ATTACK: (set(), set()),
    ActionKind.USE_SKILL: ({"skill_id"}, set()),
    ActionKind.TELEPORT: ({"destination_id"}, set()),
    ActionKind.PAUSE: ({"reason"}, {"resume_on"}),
    ActionKind.STOP: ({"reason"}, set()),
}

RESUME_POLICIES = ("dialog_closed", "operator")


def validate_params(kind: ActionKind, params: Mapping[str, Any]) -> list[str]:
    """Valida a forma da intencao. Lista vazia = valida."""
    required, optional = _SCHEMAS[kind]
    errors = [f"parametro obrigatorio ausente: '{k}'" for k in sorted(required - params.keys())]
    errors += [f"parametro desconhecido: '{k}'" for k in sorted(params.keys() - required - optional)]
    if errors:
        return errors
    checks: list[Optional[str]] = []
    if kind is ActionKind.MOVE_TO:
        checks = [_number(params, k) for k in ("x", "y", "z")]
    elif kind is ActionKind.FOLLOW:
        checks = [_text(params, "character"), _number(params, "distance")]
        if checks[1] is None and params["distance"] <= 0:
            checks.append("'distance' deve ser positiva")
    elif kind is ActionKind.SELECT_TARGET:
        if "name" not in params and "npc_id" not in params:
            checks.append("informe 'name' ou 'npc_id'")
        if "name" in params:
            checks.append(_text(params, "name"))
        if "npc_id" in params:
            checks.append(_positive_int(params, "npc_id"))
    elif kind is ActionKind.USE_SKILL:
        checks = [_positive_int(params, "skill_id")]
    elif kind is ActionKind.TELEPORT:
        checks = [_positive_int(params, "destination_id")]
    elif kind is ActionKind.PAUSE:
        checks = [_text(params, "reason")]
        if params.get("resume_on", "dialog_closed") not in RESUME_POLICIES:
            checks.append(f"'resume_on' deve ser um de {RESUME_POLICIES}")
    elif kind is ActionKind.STOP:
        checks = [_text(params, "reason")]
    return [c for c in checks if c]
