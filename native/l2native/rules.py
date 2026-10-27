"""Regras tipadas e serializaveis para os editores de politica (UX-001 item 4).

Uma `Rule` descreve "usar habilidade/item/acao X, com prioridade, atraso,
intervalo, alvo e condicoes". Condicoes tem campo, operador e valor TIPADOS;
avaliadas sobre um GameState devolvem True, False ou None (desconhecido).
Regras so configuram: nenhuma acao e executada a partir delas nesta fase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .model import GameState


class FieldType(str, Enum):
    NUMBER = "number"
    BOOL = "bool"


class ConditionField(str, Enum):
    HP_PCT = "hp_pct"
    MP_PCT = "mp_pct"
    LEVEL = "level"
    IN_COMBAT = "in_combat"
    QUEST_DIALOG = "quest_dialog"
    HAS_TARGET = "has_target"

    @property
    def type(self) -> FieldType:
        return FieldType.NUMBER if self in (ConditionField.HP_PCT, ConditionField.MP_PCT,
                                            ConditionField.LEVEL) else FieldType.BOOL

    @property
    def label(self) -> str:
        return {"hp_pct": "HP %", "mp_pct": "MP %", "level": "Nivel", "in_combat": "Em combate",
                "quest_dialog": "Janela de missao", "has_target": "Tem alvo"}[self.value]


class Operator(str, Enum):
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    EQ = "=="
    NE = "!="


_BOOL_OPERATORS = (Operator.EQ, Operator.NE)


def _reading(fld: ConditionField, state: GameState) -> Optional[Any]:
    """Valor atual do campo, ou None se desconhecido. HP/MP no GameState sao fracoes 0..1."""
    if fld is ConditionField.HP_PCT:
        return state.hp.value * 100 if state.hp.known else None
    if fld is ConditionField.MP_PCT:
        return state.mp.value * 100 if state.mp.known else None
    if fld is ConditionField.LEVEL:
        return state.level.value if state.level.known else None
    if fld is ConditionField.IN_COMBAT:
        return state.in_combat.value if state.in_combat.known else None
    if fld is ConditionField.QUEST_DIALOG:
        return state.quest_dialog.value if state.quest_dialog.known else None
    return bool(state.target.value) if state.target.known else None


@dataclass(frozen=True)
class Condition:
    field: ConditionField
    op: Operator
    value: Any

    def __post_init__(self) -> None:
        if self.field.type is FieldType.BOOL:
            if not isinstance(self.value, bool):
                raise ValueError(f"{self.field.value} exige valor booleano")
            if self.op not in _BOOL_OPERATORS:
                raise ValueError(f"{self.field.value} aceita apenas == e !=")
        else:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError(f"{self.field.value} exige valor numerico")
            if self.field in (ConditionField.HP_PCT, ConditionField.MP_PCT) and not 0 <= self.value <= 100:
                raise ValueError(f"{self.field.value} deve estar entre 0 e 100")

    def evaluate(self, state: GameState) -> Optional[bool]:
        current = _reading(self.field, state)
        if current is None:
            return None
        return {
            Operator.LT: current < self.value, Operator.LE: current <= self.value,
            Operator.GT: current > self.value, Operator.GE: current >= self.value,
            Operator.EQ: current == self.value, Operator.NE: current != self.value,
        }[self.op]

    def describe(self) -> str:
        value = ("sim" if self.value else "nao") if isinstance(self.value, bool) else self.value
        return f"{self.field.label} {self.op.value} {value}"

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field.value, "op": self.op.value, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Condition":
        return cls(ConditionField(d["field"]), Operator(d["op"]), d["value"])


class RuleKind(str, Enum):
    SKILL = "skill"
    ITEM = "item"
    ACTION = "action"


class TargetKind(str, Enum):
    SELF = "self"
    TARGET = "target"
    PARTY_MEMBER = "party_member"
    PET = "pet"


@dataclass(frozen=True)
class Rule:
    kind: RuleKind
    name: str
    target: TargetKind = TargetKind.SELF
    priority: int = 50
    delay_ms: int = 0
    interval_ms: int = 0
    enabled: bool = True
    conditions: tuple[Condition, ...] = field(default_factory=tuple)
    # O ID no jogo NAO fica na regra: vem do catalogo de habilidades do perfil (pelo nome).

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("regra precisa de nome")
        if not 0 <= self.priority <= 100:
            raise ValueError("prioridade deve estar entre 0 e 100")
        for key in ("delay_ms", "interval_ms"):
            v = getattr(self, key)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"{key} deve ser inteiro >= 0")

    def evaluate(self, state: GameState) -> Optional[bool]:
        """E logico das condicoes: False se alguma e falsa; None se alguma e
        desconhecida (e nenhuma falsa); True so se todas sao verdadeiras."""
        if not self.enabled:
            return False
        results = [c.evaluate(state) for c in self.conditions]
        if any(r is False for r in results):
            return False
        if any(r is None for r in results):
            return None
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "name": self.name, "target": self.target.value,
                "priority": self.priority, "delay_ms": self.delay_ms, "interval_ms": self.interval_ms,
                "enabled": self.enabled,
                "conditions": [c.to_dict() for c in self.conditions]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Rule":
        if "game_id" in d:
            raise ValueError("regra com game_id: formato v1, carregue pelo Profile para migrar")
        return cls(RuleKind(d["kind"]), d["name"], TargetKind(d.get("target", "self")), d.get("priority", 50),
                   d.get("delay_ms", 0), d.get("interval_ms", 0), d.get("enabled", True),
                   tuple(Condition.from_dict(c) for c in d.get("conditions", [])))


def ordered(rules: list[Rule]) -> list[Rule]:
    """Ordem de precedencia explicita: prioridade maior primeiro; empate mantem a ordem da tabela."""
    return sorted(rules, key=lambda r: -r.priority)


@dataclass(frozen=True)
class FilterRule:
    """Linha de filtro (Busca de alvo > Filtro, Drop > Filtro): casa por nome e/ou ID."""
    action: str
    name: str = ""
    game_id: Optional[int] = None
    priority: int = 50

    def __post_init__(self) -> None:
        if not self.name.strip() and self.game_id is None:
            raise ValueError("filtro precisa de nome ou ID")
        if self.game_id is not None and (isinstance(self.game_id, bool) or not isinstance(self.game_id, int)
                                         or self.game_id <= 0):
            raise ValueError("ID deve ser inteiro positivo")
        if not 0 <= self.priority <= 100:
            raise ValueError("prioridade deve estar entre 0 e 100")

    def matches(self, name: Optional[str], game_id: Optional[int]) -> bool:
        if self.game_id is not None and game_id != self.game_id:
            return False
        if self.name.strip() and (name or "").casefold() != self.name.strip().casefold():
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "name": self.name, "game_id": self.game_id, "priority": self.priority}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FilterRule":
        return cls(d["action"], d.get("name", ""), d.get("game_id"), d.get("priority", 50))


# --- motor de regras (decisao apenas) -------------------------------------------
class DecisionStatus(str, Enum):
    READY = "pronta"
    WAITING_DELAY = "aguardando atraso"
    COOLDOWN = "em intervalo"
    NO_ID = "sem ID confirmado"
    FALSE = "condicao falsa"
    UNKNOWN = "condicao desconhecida"
    DISABLED = "desativada"


@dataclass(frozen=True)
class RuleDecision:
    section: str
    rule: Rule
    status: DecisionStatus
    game_id: Optional[int] = None
    wait_ms: int = 0


class RuleEngine:
    """Decide, por secao, qual regra estaria pronta agora. NAO executa nada:
    quem consome decide se transforma a decisao em intencao (e so ha backend
    simulado ou nenhum). Ordem: prioridade maior primeiro; empate = ordem da tabela.

    - delay_ms: a condicao precisa ficar verdadeira por esse tempo seguido;
    - interval_ms: tempo minimo entre dois usos (registrados com mark_used);
    - sem ID confirmado no catalogo a regra nunca fica pronta.
    """

    def __init__(self, resolve_game_id: Callable[[Rule], Optional[int]]) -> None:
        self._resolve = resolve_game_id
        self._true_since: dict[tuple[str, str], int] = {}
        self._last_used: dict[tuple[str, str], int] = {}

    def decide(self, section: str, rules: list[Rule], state: GameState, now_ms: int) -> list[RuleDecision]:
        out = []
        for rule in ordered(rules):
            key = (section, rule.name.casefold())
            truth = rule.evaluate(state)
            if truth is True:
                self._true_since.setdefault(key, now_ms)
            else:
                self._true_since.pop(key, None)
            gid = self._resolve(rule)
            if not rule.enabled:
                status = DecisionStatus.DISABLED
            elif truth is None:
                status = DecisionStatus.UNKNOWN
            elif truth is False:
                status = DecisionStatus.FALSE
            elif gid is None:
                status = DecisionStatus.NO_ID
            else:
                waited = now_ms - self._true_since[key]
                since_use = now_ms - self._last_used[key] if key in self._last_used else None
                if since_use is not None and since_use < rule.interval_ms:
                    out.append(RuleDecision(section, rule, DecisionStatus.COOLDOWN, gid, rule.interval_ms - since_use))
                    continue
                if waited < rule.delay_ms:
                    out.append(RuleDecision(section, rule, DecisionStatus.WAITING_DELAY, gid, rule.delay_ms - waited))
                    continue
                status = DecisionStatus.READY
            out.append(RuleDecision(section, rule, status, gid))
        return out

    @staticmethod
    def chosen(decisions: list[RuleDecision]) -> Optional[RuleDecision]:
        return next((d for d in decisions if d.status is DecisionStatus.READY), None)

    def mark_used(self, section: str, rule: Rule, now_ms: int) -> None:
        self._last_used[(section, rule.name.casefold())] = now_ms


def state_time_ms(state: GameState, fallback_tick_ms: int = 500) -> int:
    """Tempo do estado em ms: timestamp ISO-8601 quando existe; senao, ciclo x passo simulado."""
    if state.timestamp:
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(state.timestamp.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            pass
    return state.tick * fallback_tick_ms
