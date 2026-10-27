"""Registro 021 (experimento 024): execucao SIMULADA das politicas do perfil
(Relaxar, Cura, Ataque, Busca de alvo com filtro, Zona, Personagem morreu),
combate do MockAdapter e protecao contra plugins lentos."""
import json
import tempfile
import unittest
from pathlib import Path

from l2native.actions import ActionKind, Intent, ResultStatus, validate_params
from l2native.adapters import MockAdapter, ReplayAdapter
from l2native.engine import Engine, EngineConfig, EngineStatus
from l2native.model import Entity, Position, observed
from l2native.plugins import PluginManager, PluginState
from l2native.profile import Profile
from l2native.script import load_script

from .helpers import FIXTURES, SCRIPTS

HUNT_PROFILE = FIXTURES / "profiles" / "cacador_simulado.json"


def hunt_scenario(**combat):
    data = json.loads((FIXTURES / "hunt_world.json").read_text(encoding="utf-8"))
    data["combat"].update(combat)
    return data


def mock_from(data):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return MockAdapter.from_scenario(path)


def run_hunter(adapter, **params):
    engine = Engine(adapter, load_script(SCRIPTS / "hunter.py"), config=EngineConfig(tick_interval=0), params=params)
    return engine, engine.run()


def policy_log(engine):
    return [r.data["message"].removeprefix("politica: ") for r in engine.journal.events("script_log")
            if r.data["message"].startswith("politica: ")]


class SimCombatTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockAdapter(self_position=Position(0, 0, 0), speed=100, capabilities=list(ActionKind)[:-2],
                                combat={"hp": 0.5, "mp": 0.5, "attack_damage": 0.5, "regen": [0, 0],
                                        "rest_regen": [0.1, 0.1],
                                        "skills": {"7": {"name": "Cura", "mp": 0.2, "heal": 0.3}},
                                        "npcs": [{"name": "Lobo", "npc_id": 5, "position": [300, 0, 0],
                                                  "damage": 0.1, "respawn": 3},
                                                 {"name": "Lobo", "npc_id": 5, "position": [2000, 0, 0]}]})
        self.n = 0

    def do(self, kind, **params):
        self.n += 1
        return self.mock.execute(Intent(self.n, kind, params))

    def test_select_attack_kill_and_respawn(self):
        s = self.mock.observe()
        self.assertEqual([n.object_id for n in s.npcs.value], [1, 2])
        self.assertEqual((s.hp.value, s.target.value, s.in_combat.value), (0.5, "", False))
        self.assertEqual(self.do(ActionKind.SELECT_TARGET, npc_id=5).detail, "alvo: Lobo")
        self.assertIs(self.mock.combat.target.object_id, 1)                # o mais proximo
        self.assertEqual(self.do(ActionKind.SELECT_TARGET, object_id=2).status, ResultStatus.COMPLETED)
        self.assertIn("fora de alcance", self.do(ActionKind.ATTACK).detail)
        self.do(ActionKind.SELECT_TARGET, object_id=1)
        self.assertEqual(self.do(ActionKind.ATTACK).status, ResultStatus.DISPATCHED)
        s = self.mock.observe()                                            # 0.5 de dano; o lobo anda ate mim
        self.assertTrue(s.in_combat.value)
        s = self.mock.observe()
        self.assertEqual(s.target.value, "")                               # morto: alvo limpo
        self.assertTrue(s.npcs.value[0].dead.value)
        self.assertEqual(self.mock.combat.kills, 1)
        for _ in range(3):
            s = self.mock.observe()
        self.assertFalse(s.npcs.value[0].dead.value)                       # renasceu no ponto original
        self.assertEqual(s.npcs.value[0].position.value, Position(300, 0, 0))

    def test_rest_skills_and_movement_block(self):
        self.assertEqual(self.do(ActionKind.USE_SKILL, skill_id=99).status, ResultStatus.FAILED)
        self.assertEqual(self.do(ActionKind.USE_SKILL, skill_id=7).status, ResultStatus.COMPLETED)
        s = self.mock.observe()
        self.assertEqual((s.hp.value, s.mp.value), (0.8, 0.3))
        self.do(ActionKind.USE_SKILL, skill_id=7)
        self.assertIn("MP insuficiente", self.do(ActionKind.USE_SKILL, skill_id=7).detail)
        self.assertEqual(self.do(ActionKind.REST, sit=True).detail, "sentada")
        self.assertIn("sentada", self.do(ActionKind.MOVE_TO, x=1, y=1, z=0).detail)
        self.assertIn("sentada", self.do(ActionKind.SELECT_TARGET, name="Lobo").detail)
        mp = self.mock.observe().mp.value
        self.assertAlmostEqual(self.mock.observe().mp.value, mp + 0.1)
        self.do(ActionKind.REST, sit=False)
        self.assertEqual(self.do(ActionKind.MOVE_TO, x=1, y=1, z=0).status, ResultStatus.DISPATCHED)

    def test_death_blocks_everything(self):
        self.mock.combat.hp, self.mock.combat.attack_damage = 0.05, 0.0
        self.do(ActionKind.SELECT_TARGET, object_id=1)
        self.do(ActionKind.ATTACK)
        for _ in range(5):                                                 # o lobo anda 80/ciclo ate o corpo a corpo
            s = self.mock.observe()
        self.assertEqual(s.hp.value, 0)
        self.assertFalse(s.in_combat.value)
        self.assertEqual(self.do(ActionKind.REST, sit=True).detail, "personagem morta")
        self.assertEqual(self.do(ActionKind.MOVE_TO, x=1, y=1, z=0).detail, "personagem morta")

    def test_observes_and_replay_keeps_unsupported(self):
        self.assertTrue({"hp", "mp", "target", "in_combat", "npcs"} <= self.mock.observes())
        self.assertNotIn("hp", MockAdapter().observes())
        replay = ReplayAdapter.from_file(FIXTURES / "quest_dialog.jsonl")
        self.addCleanup(replay.close)
        self.assertEqual(replay.execute(Intent(1, ActionKind.REST, {"sit": True})).status, ResultStatus.UNSUPPORTED)
        self.assertEqual(validate_params(ActionKind.REST, {"sit": True}), [])
        self.assertEqual(Entity("x", observed(Position(0, 0, 0), "t"), object_id=3).to_dict()["object_id"], 3)


class HunterTest(unittest.TestCase):
    def test_full_hunt_uses_every_policy_and_pauses_for_quest(self):
        adapter = MockAdapter.from_scenario(FIXTURES / "hunt_world.json")
        engine, report = run_hunter(adapter)
        self.assertEqual(report.status, EngineStatus.STOPPED, report.reason)
        self.assertIn("3 alvos derrotados", report.reason)
        self.assertEqual(adapter.combat.kills, 3)
        log = policy_log(engine)
        for expected in ("busca de alvo: selecionar Lobo", "ataque: atacar Lobo", "ataque: Golpe Forte (ID 900002)",
                         "cura: Cura Leve (ID 900001)", "relaxar: levantar"):
            self.assertIn(expected, log)
        self.assertTrue(any(m.startswith("relaxar: sentar") for m in log), log)
        self.assertTrue(any(m.startswith("ataque: aproximar") for m in log), log)
        self.assertEqual(len(engine.journal.events("paused")), 1)          # missao continua manual
        selected = {i.params.get("object_id") for i in adapter.executed if i.kind is ActionKind.SELECT_TARGET}
        self.assertNotIn(4, selected)                                      # Guarda: filtro ignorar
        self.assertNotIn(3, selected)                                      # lobo ao lado da jogadora
        self.assertNotIn(5, selected)                                      # Urso: fora da zona
        discards = [r.data["discards"] for r in engine.journal.events("script_log") if "discards" in r.data][0]
        self.assertIn("Guarda: filtro: ignorar", discards)
        self.assertIn("Lobo: jogador por perto", discards)
        self.assertIn("Urso: fora da zona", discards)

    def test_death_policy_stop_or_pause(self):
        for fallback, status in (("parar", EngineStatus.STOPPED), ("ir_para_cidade", None)):
            with self.subTest(fallback=fallback):
                profile = Profile.load(HUNT_PROFILE)
                profile.set("cura.morte", "fallback", fallback)
                profile.set_rules("cura", [])
                profile.set("cura.relaxar", "sit_hp_below", 0)
                profile.set("cura.relaxar", "sit_mp_below", 0)
                adapter = mock_from({**hunt_scenario(hp=0.2), "facts_at": {}})
                for n in adapter.combat.npcs:
                    n.damage = 0.3
                engine = Engine(adapter, load_script(SCRIPTS / "hunter.py"),
                                config=EngineConfig(tick_interval=0, max_ticks=60), params={"profile": profile.to_dict()})
                if status:
                    report = engine.run()
                    self.assertIn("personagem morreu", report.reason)
                else:
                    engine.start()
                    while engine.step() and not engine.pause_info:
                        pass
                    self.assertEqual(engine.pause_info["resume_on"], "operator")
                    self.assertIn("teleporte", engine.pause_info["reason"])
                    engine.cancel()
                    self.assertFalse(engine.step())
                    self.assertEqual(engine.status, EngineStatus.STOPPED)

    def test_unknown_vitals_never_decide_and_unsupported_backend_is_visible(self):
        # Replay de missao nao tem HP/MP/NPCs: nenhuma politica age; nada e executado
        replay = ReplayAdapter.from_file(FIXTURES / "quest_dialog.jsonl")
        engine, report = run_hunter(replay, max_idle=1000)
        log = policy_log(engine)
        self.assertTrue(log and all("desconhecid" in m for m in log), log)
        self.assertEqual(engine.journal.events("intent") and
                         {r.data["intent"]["kind"] for r in engine.journal.events("intent")}, {"pause"})

    def test_rest_without_backend_is_reported_not_faked(self):
        adapter = mock_from({**hunt_scenario(mp=0.1), "capabilities": ["move_to", "select_target", "attack"]})
        engine, report = run_hunter(adapter, max_idle=5)
        rests = [r.data for r in engine.journal.events("result") if r.data["intent"]["kind"] == "rest"]
        self.assertTrue(rests)
        self.assertTrue(all(r["status"] == "unsupported" for r in rests))


class SlowPluginTest(unittest.TestCase):
    def test_slow_plugin_is_reported_then_disabled(self):
        events = []
        clock = iter([0.0, 0.2, 1.0, 1.001, 2.0, 2.2, 3.0, 3.2, 4.0, 4.2])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "managed"
            src = Path(tmp) / "slow"
            src.mkdir()
            (src / "plugin.json").write_text(json.dumps({"id": "slow", "name": "Lento", "version": "1", "api_version": 1,
                                                         "entry": "plugin.py", "permissions": ["journal:read"]}))
            (src / "plugin.py").write_text("def activate(api):\n    pass\n\ndef on_record(record):\n    pass\n")
            pm = PluginManager(root, notify=lambda e, d: events.append((e, d)), clock=lambda: next(clock))
            pm.install(src, confirmed=True)
            pm.enable("slow", confirmed=True)
            for _ in range(5):
                pm.dispatch(object())
            info = pm.plugins["slow"]
        slow = [d for e, d in events if e == "plugin_slow"]
        self.assertEqual([d["seguidas"] for d in slow], [1, 1, 2, 3])        # a chamada rapida zera a contagem
        self.assertEqual(slow[0]["ms"], 200.0)
        self.assertEqual(info.state, PluginState.ERROR)
        self.assertIn("travaria a interface", info.error)


if __name__ == "__main__":
    unittest.main()
