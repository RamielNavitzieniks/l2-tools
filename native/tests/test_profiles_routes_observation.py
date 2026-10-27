"""Registro 022 (experimento 023): perfil v2 com migracao e catalogo de
habilidades, motor de regras (decisao apenas), catalogo de entidades, grafo
de rota, script de rota no simulado e contrato minimo de observacao."""
import json
import tempfile
import unittest
from pathlib import Path

from l2native.adapters import MockAdapter, ReplayAdapter
from l2native.adapters.live import LiveAdapter
from l2native.catalog import EntityCatalog
from l2native.engine import Engine, EngineConfig, EngineStatus
from l2native.geometry import distance_xy
from l2native.model import Entity, GameState, Position, observed, unknown
from l2native.observation import ObservationMonitor, ReadinessStatus, Requirement
from l2native.profile import FORMAT, SCHEMA_VERSION, Profile, SkillEntry, migrate
from l2native.routes import RouteGraph
from l2native.rules import (Condition, ConditionField, DecisionStatus, Operator, Rule, RuleEngine, RuleKind,
                            state_time_ms)
from l2native.script import load_script

from .helpers import FIXTURES, SCRIPTS, make_script

PROFILES = FIXTURES / "profiles"


class ProfileV2Test(unittest.TestCase):
    def test_v2_fixture_roundtrip_and_sections(self):
        p = Profile.load(PROFILES / "exemplo_v2.json")
        self.assertEqual(p.migrated, [])
        self.assertEqual(FORMAT, f"l2tools-profile/{SCHEMA_VERSION}")
        # Fight / Path / Mons / Drop / Party / Relax / Death
        self.assertEqual([r.name for r in p.rules["ataque"]], ["Golpe Forte", "Golpe Basico"])
        self.assertEqual(p.get("rota", "route_file"), "fixtures/route_demo.json")
        self.assertEqual([f.name for f in p.filters["alvo.filtro"]], ["Guarda", "Lobo"])
        self.assertEqual(p.filters["drop.filtro"][0].action, "coletar")
        self.assertEqual(p.get("grupo", "follow_distance"), 300)
        self.assertEqual(p.get("cura.relaxar", "sit_hp_below"), 35)
        self.assertEqual(p.get("cura.morte", "pause_after_res_s"), 10)
        with tempfile.TemporaryDirectory() as tmp:
            p.save(Path(tmp) / "p.json")
            self.assertEqual(Profile.load(Path(tmp) / "p.json").to_dict(), p.to_dict())
        self.assertEqual(p.script_params()["route"], "fixtures/route_demo.json")

    def test_v1_migrates_ids_into_catalog_without_touching_input(self):
        raw = json.loads((PROFILES / "exemplo_v1.json").read_text(encoding="utf-8"))
        before = json.dumps(raw, sort_keys=True)
        p = Profile.from_dict(raw)
        self.assertEqual(json.dumps(raw, sort_keys=True), before)
        self.assertEqual(p.migrated, ["v1->v2"])
        self.assertEqual(p.skill("cura leve"), SkillEntry("Cura Leve", 900001, True, "migrado da v1 (cura)"))
        self.assertIsNone(p.skill("Golpe Basico"))       # sem ID na v1: nada inventado
        self.assertEqual(p.to_dict()["format"], FORMAT)
        self.assertNotIn("game_id", json.dumps(p.to_dict()["rules"]))
        # o v2 sintetico equivale ao v1 migrado, exceto notas e a entrada nao confirmada
        v2 = Profile.load(PROFILES / "exemplo_v2.json")
        self.assertEqual(v2.rules, p.rules)
        self.assertEqual(v2.resolve_game_id(v2.rules["ataque"][0]), p.resolve_game_id(p.rules["ataque"][0]))

    def test_migration_errors(self):
        v1 = {"format": "l2tools-profile/1", "rules": {
            "cura": [{"kind": "skill", "name": "Cura", "game_id": 1}],
            "buff": [{"kind": "skill", "name": "Cura", "game_id": 2}]}}
        with self.assertRaisesRegex(ValueError, "IDs diferentes"):
            Profile.from_dict(v1)
        with self.assertRaisesRegex(ValueError, "mais nova"):
            migrate({"format": f"l2tools-profile/{SCHEMA_VERSION + 1}"})
        with self.assertRaisesRegex(ValueError, "nao reconhecido"):
            migrate({"format": "l2tools-profile/x"})
        with self.assertRaisesRegex(ValueError, "v1"):
            Rule.from_dict({"kind": "skill", "name": "Cura", "game_id": 1})

    def test_skill_catalog_rules(self):
        with self.assertRaisesRegex(ValueError, "sem ID"):
            SkillEntry("Cura", None, True)
        with self.assertRaises(ValueError):
            SkillEntry("Cura", -3)
        p = Profile.default()
        with self.assertRaisesRegex(ValueError, "repetidos"):
            p.set_skills([SkillEntry("Cura", 1), SkillEntry("cura", 2)])
        p.set_skills([SkillEntry("Cura", 7, False)])
        rule = Rule(RuleKind.SKILL, "Cura")
        self.assertIsNone(p.resolve_game_id(rule))     # nao confirmado
        p.set_skills([SkillEntry("Cura", 7, True)])
        self.assertEqual(p.resolve_game_id(rule), 7)
        self.assertTrue(any("Catalogo" in c for c in Profile.default().diff(p)))


def st(tick, **facts):
    return GameState(tick=tick, **{k: observed(v, "t") for k, v in facts.items()})


class RuleEngineTest(unittest.TestCase):
    def setUp(self):
        self.ids = {"cura": 10, "pocao": 11}
        self.engine = RuleEngine(lambda r: self.ids.get(r.name.casefold()))
        low_hp = (Condition(ConditionField.HP_PCT, Operator.LT, 50),)
        self.cura = Rule(RuleKind.SKILL, "Cura", priority=90, delay_ms=1000, interval_ms=3000, conditions=low_hp)
        self.pocao = Rule(RuleKind.ITEM, "Pocao", priority=50, conditions=low_hp)

    def decide(self, state, now):
        return {d.rule.name: d for d in self.engine.decide("cura", [self.pocao, self.cura], state, now)}

    def test_delay_priority_and_cooldown(self):
        d = self.decide(st(1, hp=0.3), 0)
        self.assertEqual(d["Cura"].status, DecisionStatus.WAITING_DELAY)
        self.assertEqual(d["Cura"].wait_ms, 1000)
        self.assertEqual(RuleEngine.chosen(list(d.values())).rule.name, "Pocao")
        d = self.decide(st(2, hp=0.3), 1000)
        chosen = RuleEngine.chosen(list(d.values()))
        self.assertEqual((chosen.rule.name, chosen.game_id), ("Cura", 10))   # prioridade maior
        self.engine.mark_used("cura", chosen.rule, 1000)
        d = self.decide(st(3, hp=0.3), 2500)
        self.assertEqual((d["Cura"].status, d["Cura"].wait_ms), (DecisionStatus.COOLDOWN, 1500))
        self.assertEqual(self.decide(st(4, hp=0.3), 4000)["Cura"].status, DecisionStatus.READY)

    def test_delay_restarts_when_condition_breaks(self):
        self.decide(st(1, hp=0.3), 0)
        self.assertEqual(self.decide(st(2, hp=0.9), 600)["Cura"].status, DecisionStatus.FALSE)
        self.assertEqual(self.decide(st(3, hp=0.3), 1200)["Cura"].status, DecisionStatus.WAITING_DELAY)

    def test_unknown_no_id_and_disabled_never_ready(self):
        d = self.decide(GameState(tick=1, hp=unknown("t")), 0)
        self.assertEqual(d["Cura"].status, DecisionStatus.UNKNOWN)
        self.ids.pop("pocao")
        self.assertEqual(self.decide(st(2, hp=0.1), 0)["Pocao"].status, DecisionStatus.NO_ID)
        off = Rule(RuleKind.SKILL, "Cura", enabled=False)
        self.assertEqual(self.engine.decide("x", [off], st(3), 0)[0].status, DecisionStatus.DISABLED)

    def test_state_time(self):
        self.assertEqual(state_time_ms(GameState(tick=4)), 2000)
        self.assertEqual(state_time_ms(GameState(tick=1, timestamp="1970-01-01T00:00:01.5Z")), 1500)


class EntityCatalogTest(unittest.TestCase):
    def test_accumulates_known_lists_only(self):
        cat = EntityCatalog()
        lobo = Entity(name="Lobo", npc_id=20120, position=observed(Position(1, 2, 3), "t"))
        ana = Entity(name="Ana", position=observed(Position(5, 5, 5), "t"))
        new = cat.update(GameState(tick=1, npcs=observed((lobo,), "t"), characters=observed((ana,), "t")))
        self.assertEqual({e.name for e in new}, {"Lobo", "Ana"})
        lobo2 = Entity(name="Lobo Velho", npc_id=20120, position=unknown("t"))
        self.assertEqual(cat.update(GameState(tick=2, npcs=observed((lobo2,), "t"), characters=unknown("t"))), [])
        e = cat.entries[("npc", 20120)]
        self.assertEqual((e.first_tick, e.last_tick, e.sightings, e.name), (1, 2, 2, "Lobo Velho"))
        self.assertEqual(e.last_position, Position(1, 2, 3))       # posicao desconhecida nao apaga a ultima
        self.assertEqual(cat.entries[("character", "ana")].sightings, 1)
        cat.update(GameState(tick=3, npcs=observed((Entity(name=None, position=unknown("t")),), "t")))
        self.assertEqual(len(cat.entries), 2)                       # sem identidade: fora


class RouteGraphTest(unittest.TestCase):
    def test_fixture_shortest_path_and_roundtrip(self):
        g = RouteGraph.load(FIXTURES / "route_demo.json")
        self.assertIn("sintetica", g.provenance)
        self.assertEqual(g.shortest_path(1, 7), [1, 5, 4, 7])
        self.assertEqual(g.shortest_path(1, 3), [1, 2, 3])
        self.assertEqual(g.nearest(Position(1390, 2010, 0)), 2)
        self.assertAlmostEqual(g.length([1, 5, 4, 7]), 400 * 2 + (2 * 400 ** 2) ** 0.5)
        with tempfile.TemporaryDirectory() as tmp:
            g.save(Path(tmp) / "r.json")
            self.assertEqual(RouteGraph.load(Path(tmp) / "r.json").to_dict(), g.to_dict())

    def test_disconnected_editing_and_validation(self):
        g = RouteGraph("teste")
        a = g.add_node(Position(0, 0, 0))
        b = g.add_node(Position(100, 0, 0), connect_to=a)
        c = g.add_node(Position(500, 500, 0))
        self.assertIsNone(g.shortest_path(a, c))
        self.assertEqual(g.shortest_path(a, a), [a])
        g.remove_node(b)
        self.assertEqual(g.edges, set())
        self.assertEqual(g.add_node(Position(1, 1, 1)), 4)          # ids nao sao reutilizados
        with self.assertRaises(KeyError):
            g.shortest_path(a, 99)
        with self.assertRaises(ValueError):
            g.connect(a, a)
        data = g.to_dict()
        with self.assertRaisesRegex(ValueError, "provenance"):
            RouteGraph.from_dict({**data, "provenance": " "})
        with self.assertRaisesRegex(ValueError, "formato"):
            RouteGraph.from_dict({**data, "format": "gps-de-terceiros"})
        with self.assertRaisesRegex(ValueError, "repetido"):
            RouteGraph.from_dict({**data, "nodes": data["nodes"] + data["nodes"][:1]})


class RouteWalkerTest(unittest.TestCase):
    def run_walker(self, **params):
        adapter = MockAdapter.from_scenario(FIXTURES / "route_world.json")
        engine = Engine(adapter, load_script(SCRIPTS / "route_walker.py"), config=EngineConfig(tick_interval=0),
                        params=params)
        return engine, engine.run(), adapter

    def test_walks_shortest_path_pausing_for_quest(self):
        engine, report, adapter = self.run_walker(goal=7)
        self.assertEqual(report.status, EngineStatus.STOPPED, report.reason)
        self.assertIn("destino alcancado: no 7", report.reason)
        moves = [(i.params["x"], i.params["y"]) for i in adapter.executed]
        self.assertEqual(moves[0], (1400, 2400))                     # no 1 ja alcancado: vai ao 5
        self.assertEqual(moves[-1], (2200, 2400))
        self.assertEqual(engine.journal.events("paused")[0].tick, 12)
        self.assertLessEqual(distance_xy(adapter.position, Position(2200, 2400, -3100)), 50)

    def test_loop_returns_and_invalid_route(self):
        _, report, adapter = self.run_walker(goal=7, loop=True, trips=2)
        self.assertIn("no 1 (trechos=2)", report.reason)
        self.assertLessEqual(distance_xy(adapter.position, Position(1000, 2000, -3100)), 50)
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "r.json"
            bad.write_text(json.dumps({"format": "l2tools-route/1", "nodes": []}), encoding="utf-8")
            _, report, _ = self.run_walker(route=str(bad))
            self.assertIn("rota invalida", report.reason)
            _, report, _ = self.run_walker(goal=99)
            self.assertIn("inexistente", report.reason)


class ObservationContractTest(unittest.TestCase):
    def full_state(self, tick):
        me = observed(Position(0, 0, 0), "mock")
        return GameState(tick=tick, self_position=me, characters=observed((), "mock"), npcs=observed((), "mock"),
                         hp=observed(1.0, "mock"), mp=observed(1.0, "mock"), target=observed("Lobo", "mock"))

    def test_insufficient_then_met(self):
        m = ObservationMonitor(min_samples=10)
        m.set_declared(frozenset({"self_position", "characters", "npcs", "hp", "mp", "target"}))
        for t in range(5):
            m.feed(self.full_state(t), 2.0)
        self.assertTrue(all(r.status is ReadinessStatus.INSUFFICIENT for r in m.report()))
        for t in range(5, 12):
            m.feed(self.full_state(t), 2.0)
        self.assertTrue(m.ready(), m.report())

    def test_unknown_fields_missing_source_errors_and_latency(self):
        m = ObservationMonitor(min_samples=10, max_p95_latency_ms=50)
        for t in range(10):
            s = self.full_state(t)
            m.feed(GameState(tick=t, self_position=s.self_position, characters=s.characters, npcs=s.npcs,
                             hp=observed(0.5, ""), mp=unknown("x"), target=s.target), 400.0)
        m.feed_error()
        report = {r.requirement: r for r in m.report()}
        self.assertEqual(report[Requirement.SELF_POSITION].status, ReadinessStatus.MET)
        self.assertEqual(report[Requirement.VITALS].status, ReadinessStatus.NOT_MET)      # MP desconhecido
        self.assertEqual(report[Requirement.PROVENANCE].status, ReadinessStatus.NOT_MET)  # HP sem fonte
        self.assertIn("10 sem fonte", report[Requirement.PROVENANCE].detail)
        self.assertEqual(report[Requirement.ADAPTER_HEALTH].status, ReadinessStatus.NOT_MET)
        self.assertIn("p95 400.0", report[Requirement.ADAPTER_HEALTH].detail)

    def test_declared_but_not_delivered_or_not_declared(self):
        m = ObservationMonitor(min_samples=1)
        m.set_declared(frozenset({"self_position"}))
        m.feed(self.full_state(1), 1.0)
        report = {r.requirement: r for r in m.report()}
        self.assertEqual(report[Requirement.SELF_POSITION].status, ReadinessStatus.MET)
        self.assertIs(report[Requirement.VITALS].declared, False)
        self.assertEqual(report[Requirement.VITALS].status, ReadinessStatus.NOT_MET)

    def test_adapters_declare_and_engine_journals_latency(self):
        self.assertEqual(LiveAdapter().observes(), frozenset())
        self.assertIsNone(ReplayAdapter.from_file(FIXTURES / "quest_dialog.jsonl").observes())
        mock = MockAdapter(facts_at={2: {"hp": 0.5}}, ticks=3)
        self.assertEqual(mock.observes(), frozenset({"self_position", "characters", "quest_dialog", "hp"}))
        self.assertNotIn("self_position", MockAdapter(self_position=None).observes())
        engine = Engine(mock, make_script(lambda bot: None), config=EngineConfig(tick_interval=0))
        engine.run()
        start = engine.journal.events("engine_start")[0].data
        self.assertEqual(start["observes"], ["characters", "hp", "quest_dialog", "self_position"])
        obs = engine.journal.events("observation")
        self.assertEqual(len(obs), 3)
        self.assertTrue(all(o.data["latency_ms"] >= 0 for o in obs))

    def test_mock_world_is_not_ready_for_live(self):
        """O simulado de rota nao fornece HP/MP/alvo/NPCs: o contrato mostra isso."""
        m = ObservationMonitor(min_samples=10)
        adapter = MockAdapter.from_scenario(FIXTURES / "route_world.json")
        m.set_declared(adapter.observes())
        for _ in range(12):
            m.feed(adapter.observe(), 1.0)
        report = {r.requirement: r.status for r in m.report()}
        self.assertEqual(report[Requirement.SELF_POSITION], ReadinessStatus.MET)
        for req in (Requirement.VITALS, Requirement.TARGET, Requirement.NEARBY_ENTITIES):
            self.assertEqual(report[req], ReadinessStatus.NOT_MET)
        self.assertFalse(m.ready())


if __name__ == "__main__":
    unittest.main()
