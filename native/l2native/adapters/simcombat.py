"""Combate SIMULADO para o MockAdapter (registro 021: execucao simulada de politicas).

Modelo deliberadamente simples e deterministico, sem nenhuma pretensao de
reproduzir formulas do jogo:

- HP/MP proprios sao fracoes 0..1 com regeneracao por ciclo (maior sentada);
- NPCs tem HP 0..1; atacar tira `attack_damage` por ciclo do alvo em alcance;
- NPC atacado fica agressivo: anda ate a personagem e causa `damage` por ciclo;
- NPC morto renasce no ponto original depois de `respawn` ciclos;
- habilidades sao definidas no cenario por ID (custo de MP, cura, dano).

Nada disso vem do cliente. Os IDs dos cenarios de teste sao ficticios.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ..actions import ActionKind, ActionResult, Intent, ResultStatus
from ..model import Entity, Position, observed

MELEE = 100.0   # distancia em que o NPC agressivo para de andar e ataca
NPC_SPEED = 80.0


def _dist(a: Position, b: Position) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _towards(a: Position, b: Position, step: float) -> Position:
    d = _dist(a, b)
    if d <= step or d == 0:
        return Position(b.x, b.y, a.z)
    f = step / d
    return Position(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f, a.z)


@dataclass
class SimNpc:
    object_id: int
    name: str
    npc_id: Optional[int]
    spawn: Position
    damage: float
    respawn: int
    position: Position
    hp: float = 1.0
    aggro: bool = False
    dead_since: Optional[int] = None

    @property
    def dead(self) -> bool:
        return self.dead_since is not None


@dataclass(frozen=True)
class SimSkill:
    name: str
    mp: float = 0.0
    heal: float = 0.0
    damage: float = 0.0
    range: float = 600.0


class CombatWorld:
    def __init__(self, spec: Mapping[str, Any], parse_position) -> None:
        self.hp = float(spec.get("hp", 1.0))
        self.mp = float(spec.get("mp", 1.0))
        self.regen = tuple(float(v) for v in spec.get("regen", (0.002, 0.004)))
        self.rest_regen = tuple(float(v) for v in spec.get("rest_regen", (0.03, 0.05)))
        self.attack_range = float(spec.get("attack_range", 600))
        self.attack_damage = float(spec.get("attack_damage", 0.25))
        self.skills = {int(k): SimSkill(**v) for k, v in spec.get("skills", {}).items()}
        self.npcs = []
        for i, n in enumerate(spec.get("npcs", []), 1):
            p = parse_position(n["position"])
            self.npcs.append(SimNpc(int(n.get("object_id", i)), n["name"], n.get("npc_id"), p,
                                    float(n.get("damage", 0.03)), int(n.get("respawn", 30)), p))
        self.target: Optional[SimNpc] = None
        self.attacking = False
        self.sitting = False
        self.kills = 0

    @property
    def dead(self) -> bool:
        return self.hp <= 0

    @property
    def in_combat(self) -> bool:
        return not self.dead and any(n.aggro and not n.dead for n in self.npcs)

    # --- ciclo ---------------------------------------------------------------
    def step(self, tick: int, me: Optional[Position]) -> None:
        for n in self.npcs:
            if n.dead and tick - n.dead_since >= n.respawn:
                n.hp, n.dead_since, n.aggro, n.position = 1.0, None, False, n.spawn
        if self.dead or me is None:
            return
        t = self.target
        if self.attacking and t and not t.dead and _dist(me, t.position) <= self.attack_range:
            self._hit(t, self.attack_damage, tick)
        for n in self.npcs:
            if not n.aggro or n.dead:
                continue
            if _dist(me, n.position) > MELEE:
                n.position = _towards(n.position, me, NPC_SPEED)
            else:
                self.hp = max(0.0, self.hp - n.damage)
        if self.dead:
            self.attacking, self.sitting = False, False
            for n in self.npcs:
                n.aggro = False
            return
        hp_r, mp_r = self.rest_regen if self.sitting else self.regen
        self.hp, self.mp = min(1.0, self.hp + hp_r), min(1.0, self.mp + mp_r)

    def _hit(self, npc: SimNpc, damage: float, tick: int) -> None:
        npc.aggro = True
        npc.hp = max(0.0, npc.hp - damage)
        if npc.hp == 0:
            npc.dead_since, npc.aggro = tick, False
            self.kills += 1
            if npc is self.target:
                self.target, self.attacking = None, False

    # --- observacao ------------------------------------------------------------
    def facts(self, src: str) -> dict[str, Any]:
        npcs = tuple(Entity(name=n.name, position=observed(n.position, src), npc_id=n.npc_id,
                            dead=observed(n.dead, src), object_id=n.object_id) for n in self.npcs)
        return {"hp": observed(round(self.hp, 4), src), "mp": observed(round(self.mp, 4), src),
                "target": observed(self.target.name if self.target else "", src),
                "in_combat": observed(self.in_combat, src), "npcs": observed(npcs, src)}

    # --- acoes ---------------------------------------------------------------------
    def blocks_movement(self) -> Optional[str]:
        if self.dead:
            return "personagem morta"
        if self.sitting:
            return "personagem sentada: levante antes (rest sit=false)"
        return None

    def execute(self, intent: Intent, me: Optional[Position], tick: int) -> Optional[ActionResult]:
        """Resultado para acoes de combate; None se a acao nao e desta parte do simulador."""
        k, p = intent.kind, intent.params

        def fail(why: str) -> ActionResult:
            return ActionResult(intent, ResultStatus.FAILED, why)
        if k not in (ActionKind.SELECT_TARGET, ActionKind.ATTACK, ActionKind.USE_SKILL, ActionKind.REST):
            return None
        if self.dead:
            return fail("personagem morta")
        if k is ActionKind.REST:
            self.sitting = bool(p["sit"])
            if self.sitting:
                self.attacking = False
            return ActionResult(intent, ResultStatus.COMPLETED, "sentada" if self.sitting else "de pe")
        if self.sitting:
            return fail("personagem sentada: levante antes (rest sit=false)")
        if me is None:
            return fail("posicao propria desconhecida no simulador")
        if k is ActionKind.SELECT_TARGET:
            alive = [n for n in self.npcs if not n.dead
                     and ("object_id" not in p or n.object_id == p["object_id"])
                     and ("npc_id" not in p or n.npc_id == p["npc_id"])
                     and ("name" not in p or n.name.casefold() == p["name"].casefold())]
            if not alive:
                return fail("nenhum NPC vivo com esse nome/ID")
            self.target = min(alive, key=lambda n: _dist(me, n.position))
            self.attacking = False
            return ActionResult(intent, ResultStatus.COMPLETED, f"alvo: {self.target.name}")
        t = self.target
        if k is ActionKind.ATTACK:
            if t is None or t.dead:
                return fail("sem alvo vivo")
            d = _dist(me, t.position)
            if d > self.attack_range:
                return fail(f"alvo fora de alcance ({d:.0f} > {self.attack_range:.0f})")
            self.attacking, t.aggro = True, True
            return ActionResult(intent, ResultStatus.DISPATCHED, "ataque automatico iniciado")
        skill = self.skills.get(p["skill_id"])
        if skill is None:
            return fail(f"habilidade {p['skill_id']} desconhecida no simulador")
        if self.mp < skill.mp:
            return fail(f"MP insuficiente para {skill.name}")
        if skill.damage:
            if t is None or t.dead:
                return fail(f"{skill.name}: sem alvo vivo")
            if _dist(me, t.position) > skill.range:
                return fail(f"{skill.name}: alvo fora de alcance")
        self.mp -= skill.mp
        if skill.heal:
            self.hp = min(1.0, self.hp + skill.heal)
        if skill.damage:
            self._hit(t, skill.damage, tick)
        return ActionResult(intent, ResultStatus.COMPLETED, f"{skill.name} usada")
