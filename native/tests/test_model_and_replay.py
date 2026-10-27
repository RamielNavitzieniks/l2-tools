import io
import unittest

from l2native.actions import ActionKind, Intent, ResultStatus, validate_params
from l2native.adapters import AdapterError, ReplayAdapter, SourceExhausted
from l2native.model import Fact, FactStatus, Position, inferred, observed, unknown


class FactTest(unittest.TestCase):
    def test_three_states_are_distinct(self):
        self.assertTrue(observed(True, "x").is_true())
        self.assertTrue(observed(False, "x").is_false())
        u = unknown("x")
        self.assertFalse(u.is_true())
        self.assertFalse(u.is_false())  # desconhecido nunca vira "falso"
        self.assertEqual(inferred(3, "calc", 0.6).status, FactStatus.INFERRED)

    def test_invariants(self):
        with self.assertRaises(ValueError):
            Fact(FactStatus.UNKNOWN, 1)
        with self.assertRaises(ValueError):
            Fact(FactStatus.OBSERVED, None)
        with self.assertRaises(ValueError):
            observed(1, "x", confidence=1.5)
        with self.assertRaises(ValueError):
            Position(float("nan"), 0, 0)


class ReplayTest(unittest.TestCase):
    def adapter(self, *lines):
        return ReplayAdapter(io.StringIO("\n".join(lines)))

    def test_parses_observed_inferred_and_unknown(self):
        a = self.adapter(
            '# comentario',
            '{"self": {"position": [1, 2, 3], "hp": 0.5}, "quest_dialog": null, "characters": [],'
            ' "level": 9, "in_combat": {"status": "inferred", "value": true, "source": "sysmsg", "confidence": 0.4}}',
        )
        s = a.observe()
        self.assertEqual(s.tick, 1)
        self.assertEqual(s.self_position.value, Position(1, 2, 3))
        self.assertEqual(s.self_position.status, FactStatus.OBSERVED)
        self.assertEqual(s.hp.value, 0.5)
        self.assertFalse(s.quest_dialog.known)           # null -> desconhecido
        self.assertFalse(s.mp.known)                     # ausente -> desconhecido
        self.assertTrue(s.characters.known)              # [] observado = ninguem a vista
        self.assertEqual(s.characters.value, ())
        self.assertFalse(s.level.known)                  # 'level' fora de 'self' nao e lido
        self.assertEqual(s.in_combat.status, FactStatus.INFERRED)
        self.assertEqual(s.in_combat.confidence, 0.4)
        with self.assertRaises(SourceExhausted):
            a.observe()

    def test_malformed_lines_are_adapter_errors_with_line_number(self):
        a = self.adapter('{"quest_dialog": true}', '{quebrado', '{"self": {"position": [1, 2]}}',
                         '{"quest_dialog": {"status": "talvez", "value": 1}}', '{"quest_dialog": false}')
        self.assertTrue(a.observe().quest_dialog.is_true())
        for line in (2, 3, 4):
            with self.assertRaisesRegex(AdapterError, f"linha {line}"):
                a.observe()
        self.assertTrue(a.observe().quest_dialog.is_false())

    def test_replay_never_executes(self):
        a = self.adapter('{}')
        self.assertEqual(a.capabilities(), frozenset())
        r = a.execute(Intent(1, ActionKind.MOVE_TO, {"x": 0, "y": 0, "z": 0}))
        self.assertEqual(r.status, ResultStatus.UNSUPPORTED)


class ValidateParamsTest(unittest.TestCase):
    def test_valid(self):
        for kind, params in [
            (ActionKind.MOVE_TO, {"x": 1, "y": 2.5, "z": -3}),
            (ActionKind.FOLLOW, {"character": "Sarynne", "distance": 250}),
            (ActionKind.SELECT_TARGET, {"npc_id": 27551}),
            (ActionKind.ATTACK, {}),
            (ActionKind.USE_SKILL, {"skill_id": 3}),
            (ActionKind.TELEPORT, {"destination_id": 7}),
            (ActionKind.PAUSE, {"reason": "x", "resume_on": "operator"}),
            (ActionKind.STOP, {"reason": "x"}),
        ]:
            with self.subTest(kind):
                self.assertEqual(validate_params(kind, params), [])

    def test_invalid(self):
        for kind, params, msg in [
            (ActionKind.MOVE_TO, {"x": 1, "y": 2}, "ausente: 'z'"),
            (ActionKind.MOVE_TO, {"x": "1", "y": 2, "z": 3}, "'x'"),
            (ActionKind.MOVE_TO, {"x": float("inf"), "y": 2, "z": 3}, "'x'"),
            (ActionKind.MOVE_TO, {"x": True, "y": 2, "z": 3}, "'x'"),
            (ActionKind.MOVE_TO, {"x": 1, "y": 2, "z": 3, "speed": 9}, "desconhecido"),
            (ActionKind.FOLLOW, {"character": " ", "distance": 250}, "'character'"),
            (ActionKind.FOLLOW, {"character": "S", "distance": -1}, "positiva"),
            (ActionKind.SELECT_TARGET, {}, "'name', 'npc_id' ou 'object_id'"),
            (ActionKind.SELECT_TARGET, {"object_id": 0}, "inteiro positivo"),
            (ActionKind.REST, {"sit": "sim"}, "booleano"),
            (ActionKind.REST, {}, "ausente: 'sit'"),
            (ActionKind.TELEPORT, {"destination_id": 0}, "inteiro positivo"),
            (ActionKind.PAUSE, {"reason": "x", "resume_on": "nunca"}, "resume_on"),
        ]:
            with self.subTest(params=params):
                errors = validate_params(kind, params)
                self.assertTrue(any(msg in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
