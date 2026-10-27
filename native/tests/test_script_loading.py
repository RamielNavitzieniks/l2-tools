import tempfile
import textwrap
import unittest
from pathlib import Path

from l2native.script import ScriptLoadError, load_script

from .helpers import SCRIPTS


class ScriptLoadingTest(unittest.TestCase):
    def write(self, code: str, name: str = "s.py") -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / name
        path.write_text(textwrap.dedent(code), encoding="utf-8")
        return path

    def test_loads_example_scripts(self):
        for name in ("quest_watch.py", "follow_sarynne.py", "circular_patrol.py"):
            script = load_script(SCRIPTS / name)
            self.assertTrue(callable(script.on_tick), name)
            self.assertEqual(script.name, name[:-3])

    def test_loads_real_python_with_optional_hooks(self):
        script = load_script(self.write("""
            import math
            from dataclasses import dataclass

            @dataclass
            class Plano:
                raio: float

            def on_start(bot): pass
            def on_tick(bot): bot.memory['r'] = Plano(math.sqrt(4)).raio
            def on_stop(bot, reason): pass
        """))
        self.assertIsNotNone(script.on_start)
        self.assertIsNotNone(script.on_stop)
        self.assertIsNone(script.on_resume)

    def test_each_load_is_isolated(self):
        a = load_script(self.write("X = 1\ndef on_tick(bot): pass\n", "a.py"))
        b = load_script(self.write("X = 2\ndef on_tick(bot): pass\n", "a.py"))
        self.assertIsNot(a.module, b.module)
        self.assertEqual((a.module.X, b.module.X), (1, 2))

    def test_rejects_invalid_scripts(self):
        cases = {
            "sem on_tick": ("answer = 42\n", "on_tick"),
            "on_tick nao chamavel": ("on_tick = 3\n", "funcao"),
            "aridade errada": ("def on_tick(): pass\n", "aceitar"),
            "on_stop aridade": ("def on_tick(b): pass\ndef on_stop(b): pass\n", "on_stop"),
            "sintaxe": ("def on_tick(bot)\n    pass\n", "SyntaxError"),
            "erro no import": ("raise RuntimeError('boom')\n", "RuntimeError: boom"),
            "modulo inexistente": ("import modulo_que_nao_existe_l2\n", "ModuleNotFoundError"),
        }
        for label, (code, message) in cases.items():
            with self.subTest(label):
                with self.assertRaisesRegex(ScriptLoadError, message):
                    load_script(self.write(code))

    def test_rejects_wrong_path(self):
        with self.assertRaisesRegex(ScriptLoadError, ".py"):
            load_script(self.write("def on_tick(b): pass\n", "s.txt"))
        with self.assertRaisesRegex(ScriptLoadError, "nao encontrado"):
            load_script(Path("nao_existe_l2native.py"))


if __name__ == "__main__":
    unittest.main()
