"""Suporte da GUI sem Qt: sessao em thread, pausa do operador, regras, perfis,
plugins, mapa e reconstrucao de estado a partir do journal."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from l2native.actions import ActionKind, CapabilityStatus, capability_status
from l2native.adapters import ReplayAdapter
from l2native.adapters.factory import SourceKind
from l2native.adapters.replay import state_from_dict
from l2native.engine import Engine, EngineConfig, EngineStatus
from l2native.mapping import MapTransform
from l2native.model import GameState, Position, observed, unknown
from l2native.plugins import ConfirmationRequired, PluginManager, PluginState
from l2native.profile import SECTIONS, Profile
from l2native.rules import Condition, ConditionField, FilterRule, Operator, Rule, RuleKind, ordered
from l2native.session import Session, SessionStatus

from .helpers import FIXTURES, ROOT, SCRIPTS, make_script


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


class OperatorPauseTest(unittest.TestCase):
    def test_operator_pause_freezes_replay_and_resumes_where_it_stopped(self):
        seen = []
        lines = "\n".join(json.dumps({"quest_dialog": False, "level": i}) for i in range(6))
        engine = Engine(ReplayAdapter(io.StringIO(lines)), make_script(lambda bot: seen.append(bot.tick)),
                        config=EngineConfig(pause_poll_interval=0.001))
        engine.start()
        engine.step()
        engine.step()
        engine.request_pause("operador")
        engine.step()                      # entra em pausa sem observar
        self.assertEqual(engine.status, EngineStatus.PAUSED)
        for _ in range(5):
            engine.step()                  # pausado: nao consome o replay
        engine.request_resume()
        while engine.step():
            pass
        self.assertEqual(seen, [1, 2, 3, 4, 5, 6])  # nenhuma linha perdida durante a pausa
        paused = engine.journal.events("paused")[0]
        self.assertEqual(paused.data["resume_on"], "operator")


class SessionTest(unittest.TestCase):
    def collect(self, session):
        events, statuses = [], []
        session.subscribe(lambda kind, p: (events if kind == "record" else statuses).append(p))
        return events, statuses

    def test_runs_in_worker_thread_until_source_ends(self):
        s = Session(SourceKind.REPLAY, FIXTURES / "quest_dialog.jsonl", SCRIPTS / "quest_watch.py",
                    config=EngineConfig(tick_interval=0))
        events, statuses = self.collect(s)
        self.assertTrue(s.start())
        self.assertTrue(s.join(5))
        self.assertEqual(s.status, SessionStatus.STOPPED)
        self.assertEqual(statuses[0], SessionStatus.RUNNING)
        self.assertIn(SessionStatus.PAUSED, statuses)
        self.assertEqual(statuses[-1], SessionStatus.STOPPED)
        self.assertIn("paused", [r.event for r in events])

    def test_pause_resume_stop(self):
        s = Session(SourceKind.MOCK, FIXTURES / "patrol_world.json", SCRIPTS / "circular_patrol.py",
                    config=EngineConfig(tick_interval=0.005, pause_poll_interval=0.005))
        s.start()
        self.assertTrue(wait_for(lambda: s.engine.ticks >= 3))
        s.pause()
        self.assertTrue(wait_for(lambda: s.status is SessionStatus.PAUSED))
        frozen = s.engine.ticks
        time.sleep(0.05)
        self.assertEqual(s.engine.ticks, frozen)
        s.resume()
        self.assertTrue(wait_for(lambda: s.engine.ticks > frozen))
        self.assertEqual(s.status, SessionStatus.RUNNING)
        s.stop()
        self.assertTrue(s.join(5))
        self.assertEqual(s.status, SessionStatus.STOPPED)
        self.assertEqual(s.report.reason, "parado pelo operador")

    def test_stop_while_paused(self):
        s = Session(SourceKind.MOCK, FIXTURES / "patrol_world.json", SCRIPTS / "circular_patrol.py",
                    config=EngineConfig(tick_interval=0.005, pause_poll_interval=0.5))
        s.start()
        self.assertTrue(wait_for(lambda: s.engine.ticks >= 1))
        s.pause()
        self.assertTrue(wait_for(lambda: s.status is SessionStatus.PAUSED))
        s.stop()
        self.assertTrue(s.join(2))
        self.assertEqual(s.status, SessionStatus.STOPPED)

    def test_failures_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.py"
            bad.write_text("def on_tick(bot):\n    print('antes do erro')\n    1 / 0\n", encoding="utf-8")
            s = Session(SourceKind.REPLAY, FIXTURES / "quest_dialog.jsonl", bad, config=EngineConfig(tick_interval=0))
            events, _ = self.collect(s)
            s.start()
            s.join(5)
        self.assertEqual(s.status, SessionStatus.FAILED)
        err = next(r for r in events if r.event == "script_error")
        self.assertEqual(err.data["line"], 3)
        out = next(r for r in events if r.event == "script_output")
        self.assertEqual(out.data["text"], "antes do erro")

    def test_invalid_script_and_live_fail_without_thread(self):
        s = Session(SourceKind.REPLAY, FIXTURES / "quest_dialog.jsonl", ROOT / "nao_existe.py")
        self.assertFalse(s.start())
        self.assertEqual(s.status, SessionStatus.FAILED)
        live = Session(SourceKind.LIVE, None, SCRIPTS / "quest_watch.py", config=EngineConfig(tick_interval=0))
        self.assertTrue(live.start())
        live.join(5)
        self.assertEqual(live.status, SessionStatus.FAILED)
        self.assertIn("nao implementado", live.report.reason)


class RulesTest(unittest.TestCase):
    def state(self, **facts):
        return GameState(tick=1, **{k: observed(v, "t") for k, v in facts.items()})

    def test_condition_three_valued(self):
        c = Condition(ConditionField.HP_PCT, Operator.LT, 40)
        self.assertTrue(c.evaluate(self.state(hp=0.3)))
        self.assertFalse(c.evaluate(self.state(hp=0.9)))
        self.assertIsNone(c.evaluate(GameState(tick=1)))

    def test_condition_types_validated(self):
        with self.assertRaises(ValueError):
            Condition(ConditionField.IN_COMBAT, Operator.LT, True)
        with self.assertRaises(ValueError):
            Condition(ConditionField.HP_PCT, Operator.LT, "40")
        with self.assertRaises(ValueError):
            Condition(ConditionField.MP_PCT, Operator.GT, 140)

    def test_rule_evaluation_and_order(self):
        heal = Rule(RuleKind.SKILL, "Cura", priority=80, conditions=(
            Condition(ConditionField.HP_PCT, Operator.LT, 50), Condition(ConditionField.IN_COMBAT, Operator.EQ, True)))
        self.assertTrue(heal.evaluate(self.state(hp=0.2, in_combat=True)))
        self.assertFalse(heal.evaluate(self.state(hp=0.2, in_combat=False)))
        self.assertIsNone(heal.evaluate(self.state(hp=0.2)))          # combate desconhecido bloqueia
        self.assertFalse(heal.evaluate(self.state(hp=0.9)))            # falsa vence desconhecida
        low = Rule(RuleKind.ITEM, "Pocao", priority=10)
        self.assertEqual([r.name for r in ordered([low, heal])], ["Cura", "Pocao"])
        self.assertEqual(Rule.from_dict(heal.to_dict()), heal)
        with self.assertRaises(ValueError):
            Rule(RuleKind.SKILL, "x", priority=101)

    def test_filter_rule(self):
        f = FilterRule("ignorar", name="Sarynne")
        self.assertTrue(f.matches("sarynne", None))
        self.assertFalse(FilterRule("atacar", game_id=27551).matches("x", 1))
        with self.assertRaises(ValueError):
            FilterRule("atacar")


class ProfileTest(unittest.TestCase):
    def test_sections_follow_inventory_without_diversos_eventos(self):
        titles = {s.title for s in SECTIONS}
        for expected in ("Geral", "Cura propria", "Relaxar", "Personagem morreu", "Buff proprio", "Cura-buff do grupo",
                         "Configuracoes do grupo", "Ataque", "Busca de alvo", "Zona de combate", "Drop"):
            self.assertIn(expected, titles)
        self.assertFalse({"Diversos", "Eventos"} & titles)

    def test_typed_settings_locked_and_roundtrip(self):
        p = Profile.default("teste", "Seguidora")
        p.set("cura.relaxar", "sit_hp_below", 35)
        with self.assertRaises(ValueError):
            p.set("cura.relaxar", "sit_hp_below", 150)
        with self.assertRaises(ValueError):
            p.set("grupo", "follow_distance", "longe")
        with self.assertRaises(ValueError):  # decisao vigente: missoes manuais
            p.set("grupo", "quest_dialogs", "auto")
        p.set("zona", "center", [100, 200, -3100])
        p.set_rules("cura", [Rule(RuleKind.SKILL, "Cura", conditions=(Condition(ConditionField.HP_PCT, Operator.LT, 50),))])
        p.set_filters("alvo.filtro", [FilterRule("ignorar", name="Guarda")])
        with self.assertRaises(ValueError):
            p.set_filters("drop.filtro", [FilterRule("atacar", name="x")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            p.save(path)
            q = Profile.load(path)
        self.assertEqual(q.to_dict(), p.to_dict())
        self.assertEqual(Profile.default().diff(Profile.default()), [])
        changes = Profile.default("teste", "Seguidora").diff(q)
        self.assertTrue(any("Sentar com HP" in c for c in changes))
        self.assertTrue(any("regras 0 -> 1" in c for c in changes))

    def test_import_rejects_credentials_and_unknown_format(self):
        data = Profile.default().to_dict()
        data["values"]["geral"]["password"] = "x"
        with self.assertRaisesRegex(ValueError, "sensivel"):
            Profile.from_dict(data)
        with self.assertRaisesRegex(ValueError, "formato"):
            Profile.from_dict({"format": "outro"})

    def test_script_params_from_group(self):
        p = Profile.default()
        p.set("grupo", "follow_distance", 300)
        self.assertEqual(p.script_params(), {"leader": "Sarynne", "distance": 300})
        p.set("grupo", "follow_leader", False)
        self.assertEqual(p.script_params(), {})


class CapabilityStatusTest(unittest.TestCase):
    def test_labels(self):
        caps = frozenset({ActionKind.MOVE_TO})
        self.assertIs(capability_status(ActionKind.MOVE_TO, None, caps), CapabilityStatus.UNKNOWN)
        self.assertIs(capability_status(ActionKind.MOVE_TO, "mock", caps), CapabilityStatus.SIMULATION)
        self.assertIs(capability_status(ActionKind.ATTACK, "mock", caps), CapabilityStatus.UNSUPPORTED)
        self.assertIs(capability_status(ActionKind.MOVE_TO, "replay", frozenset()), CapabilityStatus.UNSUPPORTED)
        self.assertIs(capability_status(ActionKind.PAUSE, "replay", frozenset()), CapabilityStatus.AVAILABLE)


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.notes = []
        self.mgr = PluginManager(Path(self.tmp.name) / "plugins", notify=lambda e, d: self.notes.append((e, d)))
        self.example = ROOT / "plugins" / "event_counter"

    def test_lifecycle_requires_confirmation(self):
        with self.assertRaises(ConfirmationRequired):
            self.mgr.install(self.example)
        info = self.mgr.install(self.example, confirmed=True)
        self.assertEqual(info.state, PluginState.DISABLED)         # instalar nao ativa
        with self.assertRaises(ConfirmationRequired):
            self.mgr.enable("event_counter")
        self.assertEqual(self.mgr.enable("event_counter", confirmed=True).state, PluginState.ENABLED)

        engine = Engine(ReplayAdapter.from_file(FIXTURES / "quest_dialog.jsonl"),
                        make_script(lambda bot: None))
        engine.run()
        for r in engine.journal.records:
            self.mgr.dispatch(r)
        logs = [d["message"] for e, d in self.notes if e == "plugin_log"]
        self.assertTrue(any(m.startswith("resumo:") and "observation=8" in m for m in logs), logs)

        self.mgr.disable("event_counter")
        self.assertEqual(self.mgr.plugins["event_counter"].state, PluginState.DISABLED)
        with self.assertRaises(ConfirmationRequired):
            self.mgr.remove("event_counter")
        self.mgr.remove("event_counter", confirmed=True)
        self.assertEqual(self.mgr.discover(), [])
        self.assertTrue(self.example.is_dir())                      # original intacto

    def make_plugin(self, pid, manifest_extra=None, code="def activate(api): pass\n"):
        folder = self.mgr.root / pid
        folder.mkdir(parents=True)
        manifest = {"id": pid, "name": pid, "version": "1", "api_version": 1, "entry": "plugin.py"}
        manifest.update(manifest_extra or {})
        (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "plugin.py").write_text(code, encoding="utf-8")

    def test_incompatible_and_broken_plugins(self):
        self.make_plugin("future", {"api_version": 9})
        self.make_plugin("game_hack", {"permissions": ["game:control"]})
        self.make_plugin("broken", code="raise RuntimeError('falha no import')\n")
        self.make_plugin("noactivate", code="X = 1\n")
        self.make_plugin("crashy", {"permissions": ["journal:read"]},
                         code="def activate(api): pass\ndef on_record(r): raise ValueError('x')\n")
        (self.mgr.root / "badjson").mkdir()
        (self.mgr.root / "badjson" / "plugin.json").write_text("{", encoding="utf-8")
        self.mgr.discover()
        st = {pid: info.state for pid, info in self.mgr.plugins.items()}
        self.assertEqual(st["future"], PluginState.INCOMPATIBLE)
        self.assertEqual(st["game_hack"], PluginState.INCOMPATIBLE)
        self.assertEqual(st["badjson"], PluginState.ERROR)
        with self.assertRaises(ValueError):
            self.mgr.enable("future", confirmed=True)
        self.assertEqual(self.mgr.enable("broken", confirmed=True).state, PluginState.ERROR)
        self.assertIn("falha no import", self.mgr.plugins["broken"].error)
        self.assertEqual(self.mgr.enable("noactivate", confirmed=True).state, PluginState.ERROR)
        self.mgr.enable("crashy", confirmed=True)
        from l2native.journal import Record
        self.mgr.dispatch(Record(1, 1, "observation", {}))
        self.assertEqual(self.mgr.plugins["crashy"].state, PluginState.ERROR)  # desativado, nao derruba nada

    def test_manifest_entry_must_stay_inside_folder(self):
        self.make_plugin("escape", {"entry": "../fora.py"})
        self.mgr.discover()
        self.assertEqual(self.mgr.plugins["escape"].state, PluginState.ERROR)


class MappingTest(unittest.TestCase):
    def test_roundtrip_and_zoom_anchor(self):
        t = MapTransform(center_x=1000, center_y=2000, scale=0.5, width=800, height=600)
        self.assertEqual(t.to_view(1000, 2000), (400, 300))
        wx, wy = t.to_world(*t.to_view(1234, 987))
        self.assertAlmostEqual(wx, 1234)
        self.assertAlmostEqual(wy, 987)
        before = t.to_world(100, 50)
        t.zoom(2.0, 100, 50)
        after = t.to_world(100, 50)
        self.assertAlmostEqual(before[0], after[0])
        self.assertAlmostEqual(before[1], after[1])
        self.assertEqual(t.scale, 1.0)

    def test_fit_contains_points(self):
        t = MapTransform(width=800, height=600)
        pts = [Position(0, 0, 0), Position(3000, 1000, 0)]
        self.assertTrue(t.fit(pts))
        for p in pts:
            vx, vy = t.to_view(p.x, p.y)
            self.assertTrue(0 <= vx <= 800 and 0 <= vy <= 600)
        self.assertFalse(t.fit([]))
        self.assertIn(MapTransform(scale=0.2).grid_step(), (200, 500, 1000))


class SnapshotTest(unittest.TestCase):
    def test_state_from_journal_summary(self):
        lines = (FIXTURES / "follow_sarynne.jsonl").read_text(encoding="utf-8")
        original = [s for s in (ReplayAdapter(io.StringIO(lines)).observe() for _ in range(1))][0]
        rebuilt = state_from_dict(original.to_dict())
        self.assertEqual(rebuilt.self_position.value, original.self_position.value)
        self.assertEqual(rebuilt.characters.value[0].name, "Sarynne")
        self.assertEqual(rebuilt.characters.value[0].position.value, Position(200, 0, -3100))
        self.assertFalse(rebuilt.hp.known)
        partial = state_from_dict({"tick": 3, "quest_dialog": unknown("v", "JOGO_SEM_FOCO").to_dict()})
        self.assertEqual(partial.quest_dialog.note, "JOGO_SEM_FOCO")


if __name__ == "__main__":
    unittest.main()
