"""Script E - Cacador com politicas do perfil (registro 021), SO no Simulado.

Executa as secoes Relaxar, Cura propria, Buff, Ataque, Busca de alvo (com
Filtro), Zona de combate e Personagem morreu pela PolicyRunner. Pausa na
janela de missao: missoes continuam manuais. Encerra apos `kills` NPCs
derrotados ou `max_idle` ciclos seguidos sem alvo valido.

Parametros: profile (dict do perfil, passado pela GUI, ou caminho de arquivo;
relativo a pasta native/ se nao existir no cwd), kills, max_idle.
"""
from pathlib import Path

from l2native.policies import PolicyRunner
from l2native.profile import Profile

DEFAULTS = {"profile": "fixtures/profiles/cacador_simulado.json", "kills": 3, "max_idle": 60}
NATIVE_DIR = Path(__file__).resolve().parents[1]


def _cfg(bot, key):
    value = bot.params.get(key)
    return DEFAULTS[key] if value in (None, "") else value


def _profile(bot):
    raw = _cfg(bot, "profile")
    if isinstance(raw, dict):
        return Profile.from_dict(raw)
    path = Path(raw)
    if not path.is_absolute() and not path.exists():
        path = NATIVE_DIR / path
    return Profile.load(path)


def on_start(bot):
    profile = _profile(bot)
    bot.memory.update(runner=PolicyRunner(profile), idle=0)
    bot.log("perfil carregado", profile=profile.name, skills=len(profile.skills))


def on_tick(bot):
    mem = bot.memory
    if bot.pause_if_quest_dialog():
        return
    runner = mem["runner"]
    decision = runner.tick(bot)
    if runner.kills >= _cfg(bot, "kills"):
        bot.stop(f"concluido: {runner.kills} alvos derrotados")
        return
    mem["idle"] = mem["idle"] + 1 if decision == "sem alvo valido" else 0
    if mem["idle"] >= _cfg(bot, "max_idle"):
        bot.stop(f"sem alvo valido ha {mem['idle']} ciclos")
