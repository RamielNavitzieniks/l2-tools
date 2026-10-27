"""Script C - Patrulha circular (porta do Teste016_CirculoContinuo.pas).

Centro = primeira posicao conhecida; `points` vertices de raio `radius`.
O proximo destino e pedido ANTES de chegar ao atual (`anticipation`), como o
DMoveTo antecipado do 016. Uma volta conta ao retornar ao vertice 0 depois da
primeira passagem. Encerra ao completar `laps`, ao sair da regiao/altura
inicial ou se um ponto nao for alcancado em `point_timeout` ciclos ativos.

Parametros: radius, points, laps, anticipation, point_timeout, max_dz.
"""
from l2native.actions import ActionKind
from l2native.geometry import circle_points, distance_xy

DEFAULTS = {"radius": 300, "points": 24, "laps": 2, "anticipation": 35, "point_timeout": 40, "max_dz": 350}


def _cfg(bot, key):
    return bot.params.get(key, DEFAULTS[key])


def _send(bot):
    mem = bot.memory
    bot.actions.move_to_position(mem["points"][mem["index"]])
    mem["sent_at"] = mem["n"]


def on_start(bot):
    bot.memory.update(n=0, laps=0)


def on_tick(bot):
    mem = bot.memory
    mem["n"] += 1

    for r in bot.last_results:
        if r.intent.kind is ActionKind.MOVE_TO and not r.status.succeeded:
            bot.stop(f"movimento nao executado ({r.status.value}): {r.detail}")
            return

    if bot.pause_if_quest_dialog():
        return

    me = bot.perception.position()
    if me is None:
        bot.log("posicao propria desconhecida: aguardando")
        return

    if "center" not in mem:
        mem["center"] = me
        mem["points"] = circle_points(me, _cfg(bot, "radius"), _cfg(bot, "points"))
        mem.update(index=0, passed_first=False)
        bot.log("centro definido", x=me.x, y=me.y, z=me.z)
        _send(bot)
        return

    center, radius = mem["center"], _cfg(bot, "radius")
    if abs(me.z - center.z) > _cfg(bot, "max_dz") or abs(me.x - center.x) > 3 * radius \
            or abs(me.y - center.y) > 3 * radius:
        bot.stop("saiu da regiao/altura inicial")
        return

    if distance_xy(me, mem["points"][mem["index"]]) <= _cfg(bot, "anticipation"):
        if mem["index"] == 0:
            if mem["passed_first"]:
                mem["laps"] += 1
                bot.log(f"voltas completadas: {mem['laps']}")
            else:
                mem["passed_first"] = True
        if mem["laps"] >= _cfg(bot, "laps"):
            bot.stop(f"concluido: {mem['laps']} voltas")
            return
        mem["index"] = (mem["index"] + 1) % len(mem["points"])
        _send(bot)
    elif mem["n"] - mem["sent_at"] > _cfg(bot, "point_timeout"):
        bot.stop(f"ponto {mem['index']} nao alcancado em {_cfg(bot, 'point_timeout')} ciclos")


def on_resume(bot):
    # A pausa pode ter interrompido o deslocamento: reenvia o destino atual e reinicia o prazo.
    if "points" in bot.memory:
        bot.log(f"retomando patrulha no ponto {bot.memory['index']} (voltas={bot.memory['laps']})")
        _send(bot)
