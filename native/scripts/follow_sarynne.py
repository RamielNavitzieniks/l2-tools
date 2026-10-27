"""Script B - Seguidor (porta do Teste017_AutofollowSarynne.pas).

Mantem ~250 unidades da lider: comeca a seguir acima de 420, para abaixo de
270 (histerese), emite no maximo uma ordem a cada `order_every` ciclos e
encerra se a lider sumir, morrer, ficar distante demais ou em outro andar.
Produz `move_to` calculado localmente; nao usa a acao `follow` do contrato.

Parametros (bot.params / --param): leader, distance, start_at, stop_at,
order_every, lost_ticks, max_distance, max_dz.
"""
from l2native.actions import ActionKind, ResultStatus
from l2native.geometry import distance_xy, follow_point

DEFAULTS = {
    "leader": "Sarynne", "distance": 250, "start_at": 420, "stop_at": 270,
    "order_every": 2, "lost_ticks": 15, "max_distance": 3000, "max_dz": 400,
}


def on_start(bot):
    bot.memory.update(n=0, following=False, last_order=None, lost_since=None, dry_run_noted=False)


def _cfg(bot, key):
    return bot.params.get(key, DEFAULTS[key])


def on_tick(bot):
    mem = bot.memory
    mem["n"] += 1  # conta apenas ciclos ativos: pausas nao consomem prazos

    for r in bot.last_results:
        if r.intent.kind is ActionKind.MOVE_TO and r.status in (ResultStatus.REJECTED, ResultStatus.FAILED):
            bot.stop(f"movimento recusado: {r.detail}")
            return
        if r.status is ResultStatus.UNSUPPORTED and not mem["dry_run_noted"]:
            mem["dry_run_noted"] = True
            bot.log("adaptador sem backend de movimento: apenas registrando intencoes")

    if bot.pause_if_quest_dialog():
        return

    me = bot.perception.position()
    if me is None:
        bot.log("posicao propria desconhecida: sem decisao neste ciclo")
        return
    if not bot.perception.characters_known():
        bot.log("lista de personagens desconhecida: sem decisao neste ciclo")
        return

    name = _cfg(bot, "leader")
    leader = bot.perception.character(name)
    if leader is None or not leader.position.known:
        if mem["lost_since"] is None:
            mem["lost_since"] = mem["n"]
            bot.log(f"{name} nao visivel; aguardando ate {_cfg(bot, 'lost_ticks')} ciclos")
        elif mem["n"] - mem["lost_since"] >= _cfg(bot, "lost_ticks"):
            bot.stop(f"{name} permaneceu fora do alcance visual")
        return
    if mem["lost_since"] is not None:
        bot.log(f"{name} encontrada novamente")
        mem["lost_since"] = None

    if leader.dead.is_true():
        bot.stop(f"{name} esta morta")
        return
    lp = leader.position.value
    if abs(lp.z - me.z) > _cfg(bot, "max_dz"):
        bot.stop("diferenca de altura grande: rota desconhecida")
        return
    dist = distance_xy(me, lp)
    if dist > _cfg(bot, "max_distance"):
        bot.stop(f"{name} distante demais para seguir diretamente ({dist:.0f})")
        return

    if dist <= _cfg(bot, "stop_at"):
        mem["following"] = False
    elif dist >= _cfg(bot, "start_at"):
        mem["following"] = True

    due = mem["last_order"] is None or mem["n"] - mem["last_order"] >= _cfg(bot, "order_every")
    if mem["following"] and due and dist > 0:
        target = follow_point(me, lp, _cfg(bot, "distance"))
        bot.actions.move_to_position(target)
        mem["last_order"] = mem["n"]
