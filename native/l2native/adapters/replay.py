"""ReplayAdapter: reproduz estados gravados em JSONL. Somente observacao.

Formato de cada linha (todos os campos opcionais; ausente = desconhecido):

    {"timestamp": "...", "self": {"position": [x, y, z], "hp": 0.9, "level": 40},
     "quest_dialog": true, "characters": [{"name": "Sarynne", "position": [x, y, z]}],
     "npcs": [...], "target": "...", "in_combat": false}

Qualquer valor pode ser substituido por um Fact explicito:
    {"status": "inferred", "value": ..., "source": "...", "confidence": 0.7}
`null` e um Fact desconhecido. Linhas vazias e linhas iniciadas por `#` sao ignoradas.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

from ..actions import ActionKind, ActionResult, Intent, ResultStatus
from ..model import Entity, Fact, FactStatus, GameState, Position, observed, unknown
from .base import AdapterError, SourceExhausted


def parse_fact(raw: Any, source: str, convert: Callable[[Any], Any] = lambda v: v) -> Fact[Any]:
    if raw is None:
        return unknown(source)
    if isinstance(raw, dict) and "status" in raw:
        status = FactStatus(raw["status"])
        if status is FactStatus.UNKNOWN:
            return unknown(raw.get("source", source), raw.get("note", ""))
        return Fact(status, convert(raw.get("value")), raw.get("source", source),
                    raw.get("confidence"), raw.get("note", ""))
    return observed(convert(raw), source)


def parse_position(raw: Any) -> Position:
    if isinstance(raw, dict):
        return Position(raw["x"], raw["y"], raw["z"])
    if isinstance(raw, (list, tuple)) and len(raw) == 3:
        return Position(*raw)
    raise ValueError(f"posicao invalida: {raw!r}")


def parse_entity(raw: Any, source: str) -> Entity:
    if not isinstance(raw, dict):
        raise ValueError(f"entidade invalida: {raw!r}")
    return Entity(
        name=raw.get("name"),
        npc_id=raw.get("npc_id"),
        position=parse_fact(raw.get("position"), source, parse_position),
        dead=parse_fact(raw.get("dead"), source, bool),
    )


def parse_state(data: dict[str, Any], tick: int, source: str) -> GameState:
    if not isinstance(data, dict):
        raise ValueError("estado deve ser objeto JSON")
    me = data.get("self") or {}
    if not isinstance(me, dict):
        raise ValueError("'self' deve ser objeto")

    def entities(raw: Any) -> Any:
        return tuple(parse_entity(e, source) for e in raw)

    return GameState(
        tick=tick,
        timestamp=data.get("timestamp"),
        self_position=parse_fact(me.get("position"), source, parse_position),
        hp=parse_fact(me.get("hp"), source, float),
        mp=parse_fact(me.get("mp"), source, float),
        level=parse_fact(me.get("level"), source, int),
        exp=parse_fact(me.get("exp"), source, float),
        target=parse_fact(data.get("target"), source, str),
        in_combat=parse_fact(data.get("in_combat"), source, bool),
        quest_dialog=parse_fact(data.get("quest_dialog"), source, bool),
        characters=parse_fact(data.get("characters"), source, entities),
        npcs=parse_fact(data.get("npcs"), source, entities),
        inventory=parse_fact(data.get("inventory"), source, dict),
        quest_info=parse_fact(data.get("quest_info"), source, dict),
    )


class ReplayAdapter:
    """Le estados de um JSONL (arquivo ou iteravel de linhas). Nao executa acoes:
    toda intencao recebe UNSUPPORTED, deixando claro que nada chegou ao jogo."""

    name = "replay"

    def __init__(self, lines: Iterable[str], source: str = "replay") -> None:
        self._lines: Iterator[tuple[int, str]] = enumerate(lines, 1)
        self._source = source
        self._tick = 0
        self._close: Optional[Callable[[], None]] = None

    @classmethod
    def from_file(cls, path: Path | str) -> "ReplayAdapter":
        path = Path(path)
        stream = path.open("r", encoding="utf-8")
        adapter = cls(stream, source=f"replay:{path.name}")
        adapter._close = stream.close
        return adapter

    def observe(self) -> GameState:
        for line_number, line in self._lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            self._tick += 1
            try:
                return parse_state(json.loads(text), self._tick, self._source)
            except (ValueError, KeyError, TypeError) as exc:
                raise AdapterError(f"linha {line_number}: {exc}") from exc
        raise SourceExhausted("fim do arquivo de replay")

    def observes(self) -> Optional[frozenset[str]]:
        return None  # depende do arquivo gravado; a prontidao e medida pelos dados

    def capabilities(self) -> frozenset[ActionKind]:
        return frozenset()

    def execute(self, intent: Intent) -> ActionResult:
        return ActionResult(intent, ResultStatus.UNSUPPORTED,
                            "replay e somente observacao; intencao registrada, nao executada")

    def close(self) -> None:
        if self._close:
            self._close()
            self._close = None


def state_from_dict(data: dict[str, Any], source: str = "snapshot") -> GameState:
    """Reconstroi um GameState a partir de GameState.to_dict() (por exemplo, o
    resumo gravado no journal). Campos ausentes voltam como desconhecidos."""

    def entities(raw: Any) -> Any:
        return tuple(parse_entity(e, source) for e in raw)

    convert: dict[str, Callable[[Any], Any]] = {
        "self_position": parse_position, "hp": float, "mp": float, "level": int, "exp": float,
        "target": str, "in_combat": bool, "quest_dialog": bool, "characters": entities,
        "npcs": entities, "inventory": dict, "quest_info": dict,
    }
    return GameState(
        tick=int(data.get("tick", 0)),
        timestamp=data.get("timestamp"),
        **{name: parse_fact(data.get(name), source, fn) for name, fn in convert.items()},
    )
