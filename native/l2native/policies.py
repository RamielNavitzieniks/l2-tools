"""Execucao das politicas do perfil (registro 021: Relaxar, Busca de alvo,
Ataque, Cura/Buff e Personagem morreu), consumida por scripts/hunter.py.

A cada ciclo decide UMA acao, nesta ordem:

1. Personagem morreu (HP observado 0): 'parar' encerra; 'ir_para_cidade' pausa
   para o operador, porque teleporte nao tem backend;
2. Relaxar: senta abaixo dos limites, levanta acima deles ou se entrar em combate;
3. Cura propria e Buff: regra escolhida pelo RuleEngine vira use_skill;
4. Ataque: com alvo, regra de ataque vira use_skill, senao attack;
5. Busca de alvo: NPC vivo na zona, permitido pelo filtro e longe de jogadores.
   Fora do alcance, aproxima com move_to. Cada descarte e registrado com o motivo;
6. Zona: sem alvo e fora do raio, volta ao centro.

Valor desconhecido nunca vira decisao: a politica correspondente espera.
Missoes continuam manuais (o script pausa na janela de missao antes daqui).
So o MockAdapter implementa estas acoes; em Replay elas ficam 'unsupported'.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .actions import ActionKind, ResultStatus
from .model import Entity, GameState, Position
from .profile import Profile
from .rules import RuleEngine, state_time_ms

SIM_TICK_MS = 500   # tempo simulado por ciclo para atraso/intervalo das regras


def _dist(a: Position, b: Position) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


@dataclass
class Discard:
    name: str
    reason: str


@dataclass
class PolicyRunner:
    profile: Profile
    rules: RuleEngine = field(init=False)
    sitting: bool = False
    selected: Optional[int] = None    # object_id do alvo selecionado (quando a fonte identifica instancias)
    attacking: bool = False           # ataque automatico pedido e aceito para o alvo atual
    kills: int = 0
    last_decision: str = ""
    last_discards: list[Discard] = field(default_factory=list)
    _blacklist: dict[object, int] = field(default_factory=dict)   # chave da entidade -> ciclo de expiracao

    def __post_init__(self) -> None:
        self.rules = RuleEngine(self.profile.resolve_game_id)

    def _v(self, section: str, key: str):
        return self.profile.get(section, key)

    # --- entrada ----------------------------------------------------------------------
    def _feedback(self, bot, state: GameState) -> None:
        for r in bot.last_results:
            k = r.intent.kind
            if k is ActionKind.REST and r.status is ResultStatus.COMPLETED:
                self.sitting = r.intent.params["sit"]
            elif k is ActionKind.REST and r.status is ResultStatus.FAILED:
                self.sitting = False
            elif k is ActionKind.ATTACK:
                self.attacking = r.status.succeeded
            elif k is ActionKind.SELECT_TARGET:
                if r.status.succeeded:
                    self.selected, self.attacking = r.intent.params.get("object_id"), False
                else:
                    self._blacklist[self._params_key(r.intent.params)] = state.tick + 20
        if not self.attacking:
            return
        npc = self._target(state)
        if npc is None or npc.dead.is_true():
            # o simulador limpa o alvo quando o NPC morre
            if self._by_oid(state, self.selected) is None or self._by_oid(state, self.selected).dead.is_true():
                self.kills += 1
                bot.log("alvo derrotado", kills=self.kills)
            self.attacking, self.selected = False, None

    @staticmethod
    def _key(npc: Entity) -> object:
        return ("oid", npc.object_id) if npc.object_id is not None else ("id", npc.npc_id, npc.name)

    @staticmethod
    def _params_key(params) -> object:
        if "object_id" in params:
            return ("oid", params["object_id"])
        return ("id", params.get("npc_id"), params.get("name"))

    @staticmethod
    def _by_oid(state: GameState, oid: Optional[int]) -> Optional[Entity]:
        if oid is None or not state.npcs.known:
            return None
        return next((n for n in state.npcs.value if n.object_id == oid), None)

    def _target(self, state: GameState) -> Optional[Entity]:
        """Entidade do alvo atual: pela instancia selecionada, senao pelo nome."""
        if not (state.target.known and state.target.value) or not state.npcs.known:
            return None
        npc = self._by_oid(state, self.selected)
        if npc is not None and npc.name == state.target.value:
            return npc
        return next((n for n in state.npcs.value if n.name == state.target.value), None)

    # --- decisao -------------------------------------------------------------------------
    def tick(self, bot) -> str:
        state: GameState = bot.perception.state
        self._feedback(bot, state)
        before = [(d.name, d.reason) for d in self.last_discards]
        decision = self._decide(bot, state)
        after = [(d.name, d.reason) for d in self.last_discards]
        if after != before and after:
            bot.log("busca de alvo: descartados", discards=[f"{n}: {r}" for n, r in after])
        if decision != self.last_decision:
            bot.log(f"politica: {decision}")
            self.last_decision = decision
        return decision

    def _rule_skill(self, bot, section: str, state: GameState, now: int) -> Optional[str]:
        if not self.profile.rules.get(section):
            return None
        chosen = RuleEngine.chosen(self.rules.decide(section, self.profile.rules[section], state, now))
        if chosen is None:
            return None
        bot.actions.use_skill(chosen.game_id)
        self.rules.mark_used(section, chosen.rule, now)
        return f"{section}: {chosen.rule.name} (ID {chosen.game_id})"

    def _decide(self, bot, state: GameState) -> str:
        hp, mp, combat = state.hp, state.mp, state.in_combat
        now = state_time_ms(state, SIM_TICK_MS)

        if hp.known and hp.value <= 0:
            if self._v("cura.morte", "fallback") == "parar":
                bot.stop("personagem morreu (Personagem morreu: parar)")
                return "morte: parar"
            bot.pause_for_manual_interaction("personagem morreu: ir_para_cidade sem backend de teleporte",
                                             resume_on="operator")
            return "morte: aguardando operador"

        # Relaxar
        if hp.known and mp.known:
            hp_pct, mp_pct = hp.value * 100, mp.value * 100
            if self.sitting:
                full = hp_pct >= self._v("cura.relaxar", "stand_hp_above") and \
                    mp_pct >= self._v("cura.relaxar", "stand_mp_above")
                if full or combat.is_true():
                    bot.actions.rest(False)
                    return "relaxar: levantar" + (" (em combate)" if not full else "")
                return "relaxar: descansando"
            low = hp_pct < self._v("cura.relaxar", "sit_hp_below") or mp_pct < self._v("cura.relaxar", "sit_mp_below")
            if low and combat.is_false():
                bot.actions.rest(True)
                self.attacking = False
                return f"relaxar: sentar (HP {hp_pct:.0f}%, MP {mp_pct:.0f}%)"
        elif self.sitting:
            return "relaxar: HP/MP desconhecidos, mantendo"

        for section in ("cura", "buff"):
            used = self._rule_skill(bot, section, state, now)
            if used:
                return used

        me = bot.perception.position()
        npc = self._target(state)
        if npc is not None and not npc.dead.is_true():
            if self.attacking:
                used = self._rule_skill(bot, "ataque", state, now)
                return used or f"ataque: atacando {npc.name}"
            if me is not None and npc.position.known and \
                    _dist(me, npc.position.value) > self._v("ataque", "attack_range"):
                return self._approach(bot, me, npc)
            bot.actions.attack()
            return f"ataque: atacar {npc.name}"

        if not state.npcs.known:
            return "busca de alvo: NPCs desconhecidos"
        if me is None:
            return "busca de alvo: posicao propria desconhecida"
        candidates = self._candidates(state, me)
        if candidates:
            npc = min(candidates, key=lambda n: _dist(me, n.position.value))
            if _dist(me, npc.position.value) > self._v("ataque", "attack_range"):
                return self._approach(bot, me, npc)
            if npc.object_id is not None:
                bot.actions.select_target(object_id=npc.object_id)
            elif npc.npc_id is not None:
                bot.actions.select_target(npc_id=npc.npc_id)
            else:
                bot.actions.select_target(name=npc.name)
            return f"busca de alvo: selecionar {npc.name}"

        center = self._v("zona", "center")
        if center and self._v("zona", "return_to_center"):
            c = Position(*center)
            if _dist(me, c) > self._v("zona", "radius"):
                bot.actions.move_to_position(c)
                return "zona: voltar ao centro"
        return "sem alvo valido"

    def _approach(self, bot, me: Position, npc: Entity) -> str:
        p = npc.position.value
        d = _dist(me, p)
        keep = self._v("ataque", "attack_range") * 0.8
        f = max(0.0, (d - keep) / d)
        bot.actions.move_to(round(me.x + (p.x - me.x) * f), round(me.y + (p.y - me.y) * f), me.z)
        return f"ataque: aproximar de {npc.name} ({d:.0f} u)"

    # --- busca de alvo ---------------------------------------------------------------------
    def _filter_action(self, npc: Entity) -> Optional[str]:
        for f in sorted(self.profile.filters.get("alvo.filtro", []), key=lambda f: -f.priority):
            if f.matches(npc.name, npc.npc_id):
                return f.action
        return None

    def _candidates(self, state: GameState, me: Position) -> list[Entity]:
        out, discards = [], []
        filters = self.profile.filters.get("alvo.filtro", [])
        whitelist = any(f.action == "atacar" for f in filters)
        center = self._v("zona", "center")
        players = state.characters.value if state.characters.known else None
        for npc in state.npcs.value:
            name = npc.name or str(npc.npc_id)
            reason = None
            action = self._filter_action(npc)
            if not npc.position.known:
                reason = "posicao desconhecida"
            elif not npc.dead.is_false():
                reason = "morto" if npc.dead.is_true() else "vida desconhecida"
            elif self._blacklist.get(self._key(npc), 0) > state.tick:
                reason = "selecao falhou ha pouco"
            elif action == "ignorar":
                reason = "filtro: ignorar"
            elif action == "revidar":
                reason = "filtro: so revidar"
            elif whitelist and action != "atacar":
                reason = "fora da lista 'atacar'"
            elif center and _dist(npc.position.value, Position(*center)) > self._v("zona", "radius"):
                reason = "fora da zona"
            elif not self._v("zona", "z_min") <= npc.position.value.z <= self._v("zona", "z_max"):
                reason = "fora dos limites de Z"
            elif self._v("alvo", "avoid_near_players"):
                if players is None:
                    reason = "jogadores desconhecidos"
                elif any(p.position.known and _dist(p.position.value, npc.position.value) <=
                         self._v("alvo", "players_radius") for p in players):
                    reason = "jogador por perto"
            if reason:
                discards.append(Discard(name, reason))
            else:
                out.append(npc)
        self.last_discards = discards
        return out
