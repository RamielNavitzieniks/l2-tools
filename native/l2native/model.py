"""Estado observado do jogo, com origem e confiabilidade de cada informacao.

Toda informacao e um `Fact`: observada, inferida ou desconhecida. Um valor
desconhecido NUNCA e substituido por um padrao plausivel (0, False, lista
vazia); quem consome decide o que fazer com a incerteza.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")


class FactStatus(str, Enum):
    OBSERVED = "observed"   # lido diretamente de uma fonte (replay, visao, API)
    INFERRED = "inferred"   # deduzido a partir de outras observacoes
    UNKNOWN = "unknown"     # a fonte nao forneceu ou nao conseguiu medir


@dataclass(frozen=True)
class Fact(Generic[T]):
    status: FactStatus
    value: Optional[T] = None
    source: str = ""
    confidence: Optional[float] = None  # 0..1 quando a fonte informa; None = nao informado
    note: str = ""

    def __post_init__(self) -> None:
        if self.status is FactStatus.UNKNOWN and self.value is not None:
            raise ValueError("Fact desconhecido nao pode carregar valor")
        if self.status is not FactStatus.UNKNOWN and self.value is None:
            raise ValueError("Fact conhecido precisa de valor")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confianca deve estar entre 0 e 1")

    @property
    def known(self) -> bool:
        return self.status is not FactStatus.UNKNOWN

    def is_true(self) -> bool:
        """True somente se o valor e conhecido e verdadeiro."""
        return self.known and self.value is True

    def is_false(self) -> bool:
        """True somente se o valor e conhecido e falso (desconhecido NAO conta)."""
        return self.known and self.value is False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status.value}
        if self.known:
            out["value"] = _plain(self.value)
        for key in ("source", "note"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.confidence is not None:
            out["confidence"] = self.confidence
        return out


def observed(value: T, source: str, confidence: Optional[float] = None, note: str = "") -> Fact[T]:
    return Fact(FactStatus.OBSERVED, value, source, confidence, note)


def inferred(value: T, source: str, confidence: Optional[float] = None, note: str = "") -> Fact[T]:
    return Fact(FactStatus.INFERRED, value, source, confidence, note)


def unknown(source: str = "", note: str = "") -> Fact[Any]:
    return Fact(FactStatus.UNKNOWN, None, source, None, note)


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        for v in (self.x, self.y, self.z):
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                raise ValueError(f"coordenada invalida: {v!r}")

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass(frozen=True)
class Entity:
    """Personagem ou NPC observado. Campos internos tambem sao Facts."""
    name: Optional[str]
    position: Fact[Position]
    npc_id: Optional[int] = None    # tipo de NPC (varios NPCs podem ter o mesmo)
    dead: Fact[bool] = field(default_factory=unknown)
    object_id: Optional[int] = None  # instancia, quando a fonte a identifica (no mock: sequencial)
    spawn: Fact[Position] = field(default_factory=unknown)   # ponto de origem, quando a fonte o informa

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"position": self.position.to_dict(), "dead": self.dead.to_dict()}
        if self.spawn.known or self.spawn.note:
            out["spawn"] = self.spawn.to_dict()
        if self.name is not None:
            out["name"] = self.name
        if self.npc_id is not None:
            out["npc_id"] = self.npc_id
        if self.object_id is not None:
            out["object_id"] = self.object_id
        return out


def _unknown_field() -> Fact[Any]:
    return unknown()


@dataclass(frozen=True)
class GameState:
    """Fotografia do jogo entregue pelo adaptador a cada ciclo.

    `characters`/`npcs` sao Facts de tupla: tupla vazia observada significa
    "ninguem a vista"; UNKNOWN significa "a fonte nao enxerga essa lista".
    """
    tick: int
    timestamp: Optional[str] = None
    self_position: Fact[Position] = field(default_factory=_unknown_field)
    hp: Fact[float] = field(default_factory=_unknown_field)
    mp: Fact[float] = field(default_factory=_unknown_field)
    level: Fact[int] = field(default_factory=_unknown_field)
    exp: Fact[float] = field(default_factory=_unknown_field)
    target: Fact[str] = field(default_factory=_unknown_field)
    in_combat: Fact[bool] = field(default_factory=_unknown_field)
    quest_dialog: Fact[bool] = field(default_factory=_unknown_field)
    characters: Fact[tuple[Entity, ...]] = field(default_factory=_unknown_field)
    npcs: Fact[tuple[Entity, ...]] = field(default_factory=_unknown_field)
    inventory: Fact[dict[str, Any]] = field(default_factory=_unknown_field)
    quest_info: Fact[dict[str, Any]] = field(default_factory=_unknown_field)

    FACT_FIELDS = (
        "self_position", "hp", "mp", "level", "exp", "target", "in_combat",
        "quest_dialog", "characters", "npcs", "inventory", "quest_info",
    )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"tick": self.tick}
        if self.timestamp:
            out["timestamp"] = self.timestamp
        for name in self.FACT_FIELDS:
            out[name] = getattr(self, name).to_dict()
        return out


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value
