"""MockAdapter: mundo simulado deterministico para testes e demonstracoes offline.

Simula apenas o necessario: a propria personagem anda em linha reta ate o
ultimo destino de `move_to` a `speed` unidades por ciclo; outras personagens
percorrem waypoints; fatos arbitrarios mudam em ciclos definidos. Nao modela
terreno, colisao, latencia nem regras do servidor.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from ..actions import ActionKind, ActionResult, Intent, ResultStatus
from ..model import Entity, GameState, Position, observed
from .base import AdapterError, SourceExhausted
from .replay import parse_fact, parse_position

_PARSERS: dict[str, Callable[[Any], Any]] = {
    "hp": float, "mp": float, "level": int, "exp": float, "target": str,
    "in_combat": bool, "quest_dialog": bool, "inventory": dict, "quest_info": dict,
}


def step_towards(current: Position, target: Position, distance: float) -> Position:
    dx, dy, dz = target.x - current.x, target.y - current.y, target.z - current.z
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length <= distance or length == 0:
        return target
    f = distance / length
    return Position(current.x + dx * f, current.y + dy * f, current.z + dz * f)


class _Walker:
    def __init__(self, waypoints: list[Position], speed: float) -> None:
        if not waypoints:
            raise ValueError("waypoints vazios")
        self.position = waypoints[0]
        self._remaining = list(waypoints[1:])
        self._speed = speed

    def advance(self) -> None:
        budget = self._speed
        while budget > 0 and self._remaining:
            nxt = self._remaining[0]
            gap = math.dist((self.position.x, self.position.y, self.position.z), (nxt.x, nxt.y, nxt.z))
            self.position = step_towards(self.position, nxt, budget)
            budget -= gap
            if self.position == nxt:
                self._remaining.pop(0)


class MockAdapter:
    name = "mock"

    def __init__(
        self,
        *,
        self_position: Optional[Position] = Position(0, 0, 0),
        speed: float = 0.0,
        characters: Optional[Mapping[str, tuple[Iterable[Position], float]]] = None,
        facts_at: Optional[Mapping[int, Mapping[str, Any]]] = None,
        capabilities: Iterable[ActionKind] = (ActionKind.MOVE_TO,),
        ticks: Optional[int] = None,
        fail_observe_ticks: Iterable[int] = (),
        execute_hook: Optional[Callable[[Intent], Any]] = None,
    ) -> None:
        self._position = self_position
        self._speed = speed
        self._destination: Optional[Position] = None
        self._walkers = {name: _Walker(list(points), spd) for name, (points, spd) in (characters or {}).items()}
        self._facts_at = {int(k): dict(v) for k, v in (facts_at or {}).items()}
        for changes in self._facts_at.values():
            invalid = set(changes) - set(_PARSERS)
            if invalid:
                raise ValueError(f"fatos nao simulaveis: {sorted(invalid)}")
        self._facts: dict[str, Any] = {"quest_dialog": False}
        self._capabilities = frozenset(capabilities)
        self._ticks = ticks
        self._fail_observe = set(fail_observe_ticks)
        self._execute_hook = execute_hook
        self.tick = 0
        self.executed: list[Intent] = []
        self.closed = False

    @classmethod
    def from_scenario(cls, path: Path | str) -> "MockAdapter":
        """Cenario JSON: {"self": {"position": [x,y,z], "speed": n},
        "characters": {"Nome": {"path": [[x,y,z], ...], "speed": n}},
        "facts_at": {"10": {"quest_dialog": true}}, "capabilities": ["move_to"], "ticks": 200}"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        me = data.get("self", {})
        return cls(
            self_position=parse_position(me["position"]) if me.get("position") is not None else None,
            speed=float(me.get("speed", 0)),
            characters={
                name: ([parse_position(p) for p in spec["path"]], float(spec.get("speed", 0)))
                for name, spec in data.get("characters", {}).items()
            },
            facts_at=data.get("facts_at"),
            capabilities=[ActionKind(c) for c in data.get("capabilities", ["move_to"])],
            ticks=data.get("ticks"),
        )

    @property
    def position(self) -> Optional[Position]:
        return self._position

    def observe(self) -> GameState:
        if self._ticks is not None and self.tick >= self._ticks:
            raise SourceExhausted("cenario simulado concluido")
        self.tick += 1
        if self.tick in self._fail_observe:
            raise AdapterError(f"falha simulada de observacao no ciclo {self.tick}")
        if self._position is not None and self._destination is not None and self._speed > 0:
            self._position = step_towards(self._position, self._destination, self._speed)
        for walker in self._walkers.values():
            walker.advance()
        self._facts.update(self._facts_at.get(self.tick, {}))

        src = "mock"
        facts = {key: parse_fact(raw, src, _PARSERS[key]) for key, raw in self._facts.items()}
        chars = tuple(Entity(name=n, position=observed(w.position, src), dead=observed(False, src))
                      for n, w in self._walkers.items())
        return GameState(
            tick=self.tick,
            self_position=observed(self._position, src) if self._position else parse_fact(None, src),
            characters=observed(chars, src),
            **facts,
        )

    def capabilities(self) -> frozenset[ActionKind]:
        return self._capabilities

    def execute(self, intent: Intent) -> ActionResult:
        self.executed.append(intent)
        if self._execute_hook is not None:
            return self._execute_hook(intent)
        if intent.kind is ActionKind.MOVE_TO:
            p = intent.params
            self._destination = Position(p["x"], p["y"], p["z"])
            return ActionResult(intent, ResultStatus.DISPATCHED, "destino aceito pelo simulador")
        return ActionResult(intent, ResultStatus.UNSUPPORTED, "simulador nao modela esta acao")

    def close(self) -> None:
        self.closed = True
