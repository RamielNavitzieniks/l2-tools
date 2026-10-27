import io
import json
import unittest

from l2native.actions import ActionResult, ResultStatus
from l2native.adapters import AdapterUnavailable, LiveAdapter, MockAdapter, ReplayAdapter
from l2native.engine import Engine, EngineConfig, EngineStatus
from l2native.model import Position

from .helpers import make_script, results


def replay(*states):
    return ReplayAdapter(io.StringIO("\n".join(json.dumps(s) for s in states)))


class SequentialEventsTest(unittest.TestCase):
    def test_full_cycle_and_journal_order(self):
        seen = []

        def on_tick(bot):
            seen.append((bot.tick, bot.perception.position()))
            bot.actions.move_to(10, 0, 0)

        adapter = MockAdapter(self_position=Position(0, 0, 0), speed=5, ticks=3)
        engine = Engine(adapter, make_script(on_tick))
        report = engine.run()

        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertIn("fonte encerrada", report.reason)
        self.assertEqual([t for t, _ in seen], [1, 2, 3])
        self.assertEqual([p.x for _, p in seen], [0, 5, 10])  # o mundo simulado respondeu ao move_to
        self.assertEqual(len(results(engine, "dispatched")), 3)
        events = [r.event for r in engine.journal.records]
        self.assertEqual(events[0], "engine_start")
        self.assertEqual(events[1:4], ["observation", "intent", "result"])
        self.assertEqual(events[-1], "engine_stop")
        self.assertTrue(adapter.closed)

    def test_unknown_data_reaches_script_as_unknown(self):
        captured = {}

        def on_tick(bot):
            captured["pos"] = bot.perception.position()
            captured["dialog_visible"] = bot.perception.quest_dialog_visible()
            captured["dialog_unknown"] = bot.perception.quest_dialog_unknown()
            captured["chars_known"] = bot.perception.characters_known()
            captured["sarynne"] = bot.perception.character("Sarynne")

        Engine(replay({}), make_script(on_tick)).run()
        self.assertEqual(captured, {"pos": None, "dialog_visible": False, "dialog_unknown": True,
                                    "chars_known": False, "sarynne": None})

    def test_max_ticks(self):
        engine = Engine(MockAdapter(), make_script(lambda bot: None), config=EngineConfig(max_ticks=4))
        report = engine.run()
        self.assertEqual((report.status, report.ticks), (EngineStatus.STOPPED, 4))


class ActionOutcomeTest(unittest.TestCase):
    def run_one(self, emit, adapter=None, **config):
        adapter = adapter or MockAdapter(ticks=1)
        engine = Engine(adapter, make_script(emit), config=EngineConfig(**config))
        engine.run()
        return engine, adapter

    def test_unsupported_action_never_reaches_adapter(self):
        def emit(bot):
            bot.actions.attack()
            bot.actions.teleport(5)
            bot.actions.follow("Sarynne", 250)

        engine, adapter = self.run_one(emit)
        self.assertEqual([r["status"] for r in results(engine)], ["unsupported"] * 3)
        self.assertEqual(adapter.executed, [])

    def test_invalid_params_rejected_before_adapter(self):
        engine, adapter = self.run_one(lambda bot: bot.actions.move_to("a", 0, 0))
        [r] = results(engine)
        self.assertEqual(r["status"], "rejected")
        self.assertEqual(adapter.executed, [])

    def test_policy_rejects_too_far_move(self):
        engine, adapter = self.run_one(lambda bot: bot.actions.move_to(9000, 0, 0), max_move_distance=5000)
        [r] = results(engine)
        self.assertEqual(r["status"], "rejected")
        self.assertIn("max_move_distance", r["detail"])
        self.assertEqual(adapter.executed, [])

    def test_adapter_exception_is_failed_not_success(self):
        def boom(intent):
            raise ConnectionError("cliente fechou")

        engine, _ = self.run_one(lambda bot: bot.actions.move_to(1, 1, 0), MockAdapter(ticks=1, execute_hook=boom))
        [r] = results(engine)
        self.assertEqual(r["status"], "failed")
        self.assertIn("ConnectionError", r["detail"])

    def test_adapter_invalid_result_is_failed(self):
        engine, _ = self.run_one(lambda bot: bot.actions.move_to(1, 1, 0),
                                 MockAdapter(ticks=1, execute_hook=lambda i: True))
        self.assertEqual(results(engine)[0]["status"], "failed")

    def test_adapter_result_for_other_intent_is_failed(self):
        def wrong(intent):
            other = type(intent)(intent.id + 99, intent.kind, intent.params)
            return ActionResult(other, ResultStatus.COMPLETED)

        engine, _ = self.run_one(lambda bot: bot.actions.move_to(1, 1, 0), MockAdapter(ticks=1, execute_hook=wrong))
        self.assertEqual(results(engine)[0]["status"], "failed")

    def test_script_sees_previous_results(self):
        seen = []

        def on_tick(bot):
            seen.append([r.status for r in bot.last_results])
            bot.actions.attack()

        Engine(MockAdapter(ticks=2), make_script(on_tick)).run()
        self.assertEqual(seen, [[], [ResultStatus.UNSUPPORTED]])

    def test_stop_interrupts_following_intents(self):
        def emit(bot):
            bot.stop("chega")
            bot.actions.move_to(1, 1, 0)

        engine, adapter = self.run_one(emit, MockAdapter(ticks=5))
        self.assertEqual([r["status"] for r in results(engine)], ["completed", "interrupted"])
        self.assertEqual(adapter.executed, [])
        self.assertEqual(engine.status, EngineStatus.STOPPED)
        self.assertIn("chega", engine.reason)
        self.assertEqual(engine.ticks, 1)


class FailureTest(unittest.TestCase):
    def test_script_error_discards_intents_and_fails(self):
        def on_tick(bot):
            bot.actions.move_to(1, 1, 0)
            raise ZeroDivisionError("bug do script")

        adapter = MockAdapter(ticks=5)
        engine = Engine(adapter, make_script(on_tick))
        report = engine.run()
        self.assertEqual(report.status, EngineStatus.FAILED)
        self.assertEqual(adapter.executed, [])
        self.assertEqual(results(engine), [])  # nenhum resultado, muito menos sucesso
        [err] = engine.journal.events("script_error")
        self.assertIn("ZeroDivisionError", err.data["error"])
        [disc] = engine.journal.events("intents_discarded")
        self.assertEqual(disc.data["intents"][0]["kind"], "move_to")

    def test_script_errors_tolerated_up_to_limit(self):
        calls = []

        def on_tick(bot):
            calls.append(bot.tick)
            if bot.tick in (2, 3):
                raise ValueError("falha transitoria")

        report = Engine(MockAdapter(ticks=5), make_script(on_tick),
                        config=EngineConfig(max_consecutive_script_errors=3)).run()
        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertEqual(calls, [1, 2, 3, 4, 5])

    def test_transient_adapter_failure_is_recorded_and_recovered(self):
        ticks = []
        adapter = MockAdapter(ticks=4, fail_observe_ticks={2})
        engine = Engine(adapter, make_script(lambda bot: ticks.append(bot.tick)))
        report = engine.run()
        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertEqual(ticks, [1, 3, 4])
        self.assertEqual(len(engine.journal.events("adapter_error")), 1)

    def test_consecutive_adapter_failures_fail_engine(self):
        adapter = MockAdapter(ticks=10, fail_observe_ticks={2, 3, 4})
        engine = Engine(adapter, make_script(lambda bot: None))
        report = engine.run()
        self.assertEqual(report.status, EngineStatus.FAILED)
        self.assertEqual(len(engine.journal.events("adapter_error")), 3)
        self.assertTrue(adapter.closed)

    def test_crashing_adapter_is_contained(self):
        class Crashy(MockAdapter):
            def observe(self):
                raise KeyError("estrutura inesperada")

        engine = Engine(Crashy(), make_script(lambda bot: None))
        self.assertEqual(engine.run().status, EngineStatus.FAILED)
        self.assertEqual(len(engine.journal.events("adapter_crash")), 3)

    def test_live_adapter_reports_unavailable(self):
        report = Engine(LiveAdapter(), make_script(lambda bot: None)).run()
        self.assertEqual(report.status, EngineStatus.FAILED)
        self.assertIn("nao implementado", report.reason)
        with self.assertRaises(AdapterUnavailable):
            LiveAdapter().observe()

    def test_on_stop_always_called_and_its_error_logged(self):
        reasons = []

        def on_stop(bot, reason):
            reasons.append(reason)
            raise RuntimeError("erro ao encerrar")

        engine = Engine(MockAdapter(ticks=1), make_script(lambda bot: None, on_stop=on_stop))
        report = engine.run()
        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertEqual(len(reasons), 1)
        self.assertEqual(engine.journal.events("script_error")[0].data["hook"], "on_stop")


class CancellationTest(unittest.TestCase):
    def test_cancel_interrupts_rest_of_tick_and_stops(self):
        holder = {}

        def on_tick(bot):
            bot.actions.move_to(1, 1, 0)
            holder["engine"].cancel("operador apertou Stop")
            bot.actions.move_to(2, 2, 0)

        adapter = MockAdapter(ticks=10)
        engine = Engine(adapter, make_script(on_tick))
        holder["engine"] = engine
        report = engine.run()
        self.assertEqual([r["status"] for r in results(engine)], ["interrupted", "interrupted"])
        self.assertEqual(adapter.executed, [])
        self.assertEqual((report.status, report.reason, report.ticks), (EngineStatus.STOPPED, "operador apertou Stop", 1))

    def test_cancel_before_start_runs_no_tick(self):
        engine = Engine(MockAdapter(ticks=10), make_script(lambda bot: None))
        engine.cancel()
        self.assertEqual(engine.run().ticks, 0)

    def test_keyboard_interrupt_is_controlled_stop(self):
        def on_tick(bot):
            raise KeyboardInterrupt

        adapter = MockAdapter(ticks=10)
        report = Engine(adapter, make_script(on_tick)).run()
        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertIn("Ctrl+C", report.reason)
        self.assertTrue(adapter.closed)


class PauseResumeTest(unittest.TestCase):
    def counting_script(self, resume_on="dialog_closed"):
        def on_start(bot):
            bot.memory["work"] = 0

        def on_tick(bot):
            if bot.perception.quest_dialog_visible():
                bot.pause_for_manual_interaction("quest", resume_on=resume_on)
                bot.actions.move_to(1, 1, 0)  # deve ser interrompida pela pausa
                return
            bot.memory["work"] += 1

        resumed = []
        return make_script(on_tick, on_start=on_start, on_resume=lambda bot: resumed.append(bot.tick)), resumed

    def test_pause_preserves_state_and_resumes_when_dialog_observed_closed(self):
        script, resumed = self.counting_script()
        adapter = replay({"quest_dialog": False}, {"quest_dialog": False}, {"quest_dialog": True},
                         {"quest_dialog": True}, {"quest_dialog": None}, {"quest_dialog": False},
                         {"quest_dialog": None}, {"quest_dialog": False}, {"quest_dialog": False},
                         {"quest_dialog": False})
        engine = Engine(adapter, script)
        engine.run()
        self.assertEqual([r["status"] for r in results(engine)], ["completed", "interrupted"])
        self.assertEqual(engine.journal.events("paused")[0].tick, 3)
        # desconhecido (t5, t7) nao conta como fechado e zera a contagem: retoma so em t9
        self.assertEqual(resumed, [9])
        self.assertEqual(engine.journal.events("resumed")[0].data["paused_since"], 3)
        self.assertEqual(engine.bot.memory["work"], 4)  # t1, t2, t9, t10: memoria preservada

    def test_operator_resume(self):
        script, resumed = self.counting_script(resume_on="operator")
        adapter = replay(*([{"quest_dialog": True}] + [{"quest_dialog": False}] * 4))
        engine = Engine(adapter, script)
        self.assertTrue(engine.start())
        for _ in range(3):
            engine.step()
        self.assertEqual(engine.status, EngineStatus.PAUSED)  # dialogo fechado nao basta neste modo
        engine.request_resume()
        engine.step()
        self.assertEqual(engine.status, EngineStatus.RUNNING)
        self.assertEqual(resumed, [4])
        self.assertEqual(engine.journal.events("resumed")[0].data["by"], "operador")

    def test_actions_from_on_resume_are_processed(self):
        def on_tick(bot):
            if bot.perception.quest_dialog_visible():
                bot.pause_for_manual_interaction("quest")

        adapter = MockAdapter(ticks=5, facts_at={1: {"quest_dialog": True}, 2: {"quest_dialog": False}})
        engine = Engine(adapter, make_script(on_tick, on_resume=lambda bot: bot.actions.move_to(5, 5, 0)))
        engine.run()
        self.assertEqual([i.params["x"] for i in adapter.executed], [5])
        self.assertEqual(engine.journal.events("resumed")[0].tick, 3)


if __name__ == "__main__":
    unittest.main()
