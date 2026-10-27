import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from l2native.adapters import MockAdapter, ReplayAdapter
from l2native.cli import main as cli_main
from l2native.engine import Engine, EngineStatus
from l2native.geometry import circle_points, distance_xy, follow_point
from l2native.model import Position
from l2native.script import load_script

from .helpers import FIXTURES, SCRIPTS, results


def run(script, adapter, **params):
    engine = Engine(adapter, load_script(SCRIPTS / script), params=params)
    return engine, engine.run()


class GeometryTest(unittest.TestCase):
    def test_follow_point_matches_teste017_formula(self):
        self.assertEqual(follow_point(Position(0, 0, 0), Position(500, 0, 10), 250), Position(250, 0, 10))
        p = follow_point(Position(0, 0, 0), Position(300, 400, 0), 250)  # distancia 500
        self.assertEqual((p.x, p.y), (150, 200))

    def test_circle_points(self):
        pts = circle_points(Position(1000, 2000, -3100), 300, 24)
        self.assertEqual(len(pts), 24)
        self.assertEqual(pts[0], Position(1300, 2000, -3100))
        for p in pts:
            self.assertAlmostEqual(distance_xy(Position(1000, 2000, 0), p), 300, delta=1)


class QuestWatchTest(unittest.TestCase):
    def test_replay_pauses_and_resumes(self):
        engine, report = run("quest_watch.py", ReplayAdapter.from_file(FIXTURES / "quest_dialog.jsonl"))
        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertEqual([r["intent"]["kind"] for r in results(engine)], ["pause"])
        self.assertEqual(engine.journal.events("paused")[0].tick, 3)
        self.assertEqual(engine.journal.events("resumed")[0].tick, 7)
        self.assertEqual(engine.bot.memory["pausas"], 1)
        logs = [r.data["message"] for r in engine.journal.events("script_log")]
        self.assertTrue(any("desconhecida" in m for m in logs))


class FollowSarynneTest(unittest.TestCase):
    def test_replay_decisions(self):
        engine, report = run("follow_sarynne.py", ReplayAdapter.from_file(FIXTURES / "follow_sarynne.jsonl"))
        moves = [(r.tick, r.data["intent"]["params"]) for r in engine.journal.events("intent")
                 if r.data["intent"]["kind"] == "move_to"]
        self.assertEqual(moves, [
            (2, {"x": 250, "y": 0, "z": -3100}),   # 500 >= 420: comeca a seguir
            (4, {"x": 550, "y": 0, "z": -3100}),   # t3 respeita order_every=2
            (7, {"x": 660, "y": 29, "z": -3100}),  # reencontrada (nome sem diferenciar caixa); histerese mantem
        ])
        # replay nao executa: todas as intencoes de movimento ficam UNSUPPORTED, nunca sucesso
        self.assertEqual({r["status"] for r in results(engine) if r["intent"]["kind"] == "move_to"}, {"unsupported"})
        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertIn("altura", report.reason)

    def test_simulated_follow_keeps_distance(self):
        adapter = MockAdapter.from_scenario(FIXTURES / "follow_world.json")
        engine, report = run("follow_sarynne.py", adapter)
        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertIn("fonte encerrada", report.reason)
        leader = Position(2000, 1500, -3100)  # ultimo waypoint, atingido no ciclo 40
        final = adapter.position
        self.assertLess(distance_xy(final, leader), 420)
        self.assertGreater(distance_xy(final, leader), 200)
        self.assertTrue(all(r["status"] == "dispatched" for r in results(engine)))

    def test_lost_leader_stops(self):
        lines = [{"self": {"position": [0, 0, 0]}, "characters": []}] * 5
        adapter = ReplayAdapter(io.StringIO("\n".join(json.dumps(x) for x in lines)))
        _, report = run("follow_sarynne.py", adapter, lost_ticks=3)
        self.assertIn("fora do alcance", report.reason)

    def test_rejected_move_stops(self):
        adapter = MockAdapter(self_position=Position(0, 0, 0), ticks=5,
                              characters={"Sarynne": ([Position(2900, 0, 0)], 0)})
        engine = Engine(adapter, load_script(SCRIPTS / "follow_sarynne.py"), params={})
        engine.config = type(engine.config)(max_move_distance=1000)
        report = engine.run()
        self.assertEqual(results(engine)[0]["status"], "rejected")
        self.assertIn("movimento recusado", report.reason)


class CircularPatrolTest(unittest.TestCase):
    def test_two_laps_in_simulation_with_quest_pause(self):
        adapter = MockAdapter.from_scenario(FIXTURES / "patrol_world.json")
        engine, report = run("circular_patrol.py", adapter)
        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertEqual(report.reason, "script solicitou parada: concluido: 2 voltas")
        center = Position(1000, 2000, -3100)
        moves = [i for i in adapter.executed]
        for m in moves:
            p = Position(m.params["x"], m.params["y"], m.params["z"])
            self.assertAlmostEqual(distance_xy(center, p), 300, delta=1)
        # sequencia de vertices percorrida: 0..23, 0..23, 0 (+ reenvio do ponto atual ao retomar)
        expected = circle_points(center, 300, 24)
        visited = [Position(m.params["x"], m.params["y"], m.params["z"]) for m in moves]
        dedup = [p for i, p in enumerate(visited) if i == 0 or p != visited[i - 1]]
        self.assertEqual(dedup, expected * 2 + [expected[0]])
        self.assertEqual(engine.journal.events("paused")[0].tick, 30)
        self.assertEqual(engine.journal.events("resumed")[0].tick, 35)
        self.assertEqual(engine.bot.memory["laps"], 2)

    def test_timeout_when_character_does_not_move(self):
        adapter = MockAdapter(self_position=Position(0, 0, 0), speed=0, ticks=100)
        _, report = run("circular_patrol.py", adapter, point_timeout=5)
        self.assertIn("nao alcancado em 5 ciclos", report.reason)

    def test_stops_when_movement_unsupported(self):
        adapter = MockAdapter(self_position=Position(0, 0, 0), speed=60, ticks=10, capabilities=())
        _, report = run("circular_patrol.py", adapter)
        self.assertIn("unsupported", report.reason)


class CliTest(unittest.TestCase):
    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli_main(list(argv))
        return code, out.getvalue()

    def test_replay_demo_with_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "j.jsonl"
            code, out = self.cli("run", "--script", str(SCRIPTS / "follow_sarynne.py"), "--adapter", "replay",
                                 "--events", str(FIXTURES / "follow_sarynne.jsonl"), "--journal", str(journal))
            self.assertEqual(code, 0)
            self.assertIn("UNSUPPORTED", out)
            records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[-1]["event"], "engine_stop")

    def test_mock_patrol_with_params(self):
        code, out = self.cli("run", "--script", str(SCRIPTS / "circular_patrol.py"), "--adapter", "mock",
                             "--scenario", str(FIXTURES / "patrol_world.json"), "--param", "laps=1", "--quiet")
        self.assertEqual(code, 0)
        self.assertIn("concluido: 1 voltas", out)

    def test_live_is_explicitly_unavailable(self):
        code, out = self.cli("run", "--script", str(SCRIPTS / "quest_watch.py"), "--adapter", "live")
        self.assertEqual(code, 3)
        self.assertIn("nao implementado", out)

    def test_invalid_script_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.py"
            bad.write_text("x = 1\n", encoding="utf-8")
            code, out = self.cli("run", "--script", str(bad), "--adapter", "live")
        self.assertEqual(code, 2)
        self.assertIn("on_tick", out)


if __name__ == "__main__":
    unittest.main()
