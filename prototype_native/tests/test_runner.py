import tempfile
import unittest
from pathlib import Path

from runner import Context, load_python_script, replay


ROOT = Path(__file__).resolve().parents[1]


class TestNativeHost(unittest.TestCase):
    def test_replays_python_and_only_records_intent(self):
        results = replay(
            ROOT / "scripts/quest_dialog_watch.py",
            ROOT / "fixtures/quest_states.jsonl",
        )
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["intents"], [])
        self.assertEqual(
            results[1]["intents"],
            [{"kind": "pause_for_manual_quest_dialog", "parameters": {}}],
        )
        self.assertEqual(results[2]["intents"], [])

    def test_dynamic_script_is_real_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "custom.py"
            script.write_text(
                "def on_tick(ctx):\n    ctx.propose('demo', n=ctx.state['n'] + 1)\n",
                encoding="utf-8",
            )
            fn = load_python_script(script)
            ctx = Context({"n": 10})
            fn(ctx)
            self.assertEqual(ctx.intents[0]["parameters"]["n"], 11)

    def test_missing_handler_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "bad.py"
            script.write_text("answer=42\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "on_tick"):
                load_python_script(script)


if __name__ == "__main__":
    unittest.main()
