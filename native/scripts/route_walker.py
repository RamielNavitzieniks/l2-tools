"""Script D - Caminhar por rota (registro 022).

Le um grafo de rota proprio (formato l2tools-route/1), calcula o menor caminho
do no mais proximo da posicao atual ate `goal` e anda no por no com
`move_to`, pedindo o proximo no ao chegar a `tolerance` do atual. Com `loop`,
volta pelo mesmo caminho ate completar `trips` trechos. Pausa na janela de
missao (missoes continuam manuais). Sem geodata: o caminho vale apenas para
os pontos desenhados pelo operador.

Parametros: route (arquivo; relativo a pasta native/ se nao existir no cwd),
goal (id do no; padrao = maior id), tolerance, loop, trips, point_timeout.
"""
from pathlib import Path

from l2native.actions import ActionKind
from l2native.geometry import distance_xy
from l2native.routes import RouteGraph

DEFAULTS = {"route": "fixtures/route_demo.json", "goal": None, "tolerance": 50, "loop": False, "trips": 2,
            "point_timeout": 40}
NATIVE_DIR = Path(__file__).resolve().parents[1]


def _cfg(bot, key):
    value = bot.params.get(key)
    return DEFAULTS[key] if value in (None, "") else value


def _load(bot):
    path = Path(_cfg(bot, "route"))
    if not path.is_absolute() and not path.exists():
        path = NATIVE_DIR / path
    return RouteGraph.load(path)


def _send(bot):
    mem = bot.memory
    bot.actions.move_to_position(mem["graph"].nodes[mem["path"][mem["index"]]])
    mem["sent_at"] = mem["n"]


def _plan(bot, me):
    mem = bot.memory
    graph = mem["graph"]
    start = graph.nearest(me)
    goal = _cfg(bot, "goal")
    goal = max(graph.nodes) if goal is None else int(goal)
    if goal not in graph.nodes:
        bot.stop(f"no de destino inexistente: {goal}")
        return False
    path = graph.shortest_path(start, goal)
    if path is None:
        bot.stop(f"sem ligacao entre os nos {start} e {goal}")
        return False
    first = 1 if len(path) > 1 and distance_xy(me, graph.nodes[path[0]]) <= _cfg(bot, "tolerance") else 0
    mem.update(path=path, index=first, trips=0)
    bot.log("caminho planejado", nodes=path, length=round(graph.length(path)))
    return True


def on_start(bot):
    bot.memory.update(n=0)


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

    if "graph" not in mem:
        try:
            mem["graph"] = _load(bot)
        except (OSError, ValueError, KeyError) as exc:
            bot.stop(f"rota invalida: {exc}")
            return
        if not mem["graph"].nodes:
            bot.stop("rota sem nos")
            return
    if "path" not in mem:
        if not _plan(bot, me):
            return
        _send(bot)
        return

    target = mem["graph"].nodes[mem["path"][mem["index"]]]
    if distance_xy(me, target) <= _cfg(bot, "tolerance"):
        if mem["index"] == len(mem["path"]) - 1:
            mem["trips"] += 1
            if not _cfg(bot, "loop") or mem["trips"] >= _cfg(bot, "trips"):
                bot.stop(f"destino alcancado: no {mem['path'][-1]} (trechos={mem['trips']})")
                return
            mem["path"] = mem["path"][::-1]
            mem["index"] = 0
            bot.log(f"trecho {mem['trips']} concluido; voltando")
        mem["index"] += 1
        _send(bot)
    elif mem["n"] - mem["sent_at"] > _cfg(bot, "point_timeout"):
        bot.stop(f"no {mem['path'][mem['index']]} nao alcancado em {_cfg(bot, 'point_timeout')} ciclos")


def on_resume(bot):
    if "path" in bot.memory:
        bot.log(f"retomando rota no no {bot.memory['path'][bot.memory['index']]}")
        _send(bot)
