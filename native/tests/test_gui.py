"""Testes da GUI PySide6 em modo offscreen (sem monitor, sem Lineage II)."""
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

from l2native.actions import ActionKind
from l2native.adapters.factory import SourceKind
from l2native.rules import Condition, ConditionField, Operator, Rule, RuleKind
from l2native.session import SessionStatus

from .helpers import FIXTURES, ROOT, SCRIPTS


@unittest.skipIf(QApplication is None, "PySide6 ausente: pip install PySide6-Essentials")
class GuiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from l2native.gui.mainwindow import MainWindow
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "cfg"
        self.answers = []
        self.MainWindow = MainWindow
        self.win = self.new_window()

    def new_window(self):
        win = self.MainWindow(config_path=self.cfg, confirm=self._confirm)
        win.profile.values["geral"]["tick_interval_ms"] = 5
        win.show()
        self.addCleanup(lambda: (win.session and win.session.stop(), win.session and win.session.join(2)))
        return win

    def _confirm(self, title, text):
        self.answers.append(title)
        return True

    def pump(self, cond, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if cond():
                for _ in range(20):
                    self.app.processEvents()
                return True
            time.sleep(0.003)
        return False

    def run_to_end(self):
        self.assertTrue(self.pump(lambda: self.win.session.finished), self.win.status_label.text())


class MainWindowTest(GuiTestCase):
    def test_layout_and_honest_initial_state(self):
        w = self.win
        self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())], ["Mapa", "Script"])
        for key in ("character", "plugins", "target", "players", "npcs", "drops", "capabilities", "events"):
            self.assertIn(key, w.docks)
        self.assertIn("Ocioso", w.status_label.text())
        self.assertNotIn("Conectado", w.status_label.text())
        self.assertEqual(w.capabilities.status_of(ActionKind.MOVE_TO), "Desconhecida")
        self.assertEqual(w.character.labels["hp"].text(), "desconhecido")
        self.assertFalse(w.actions_run["pause"].isEnabled())
        self.assertFalse(w.manual_action.isEnabled())

    def test_replay_example_quest_pause_and_resume(self):
        w = self.win
        w.load_example("scripts/quest_watch.py", SourceKind.REPLAY, "fixtures/quest_dialog.jsonl")
        self.assertIsNone(w.session)  # carregar exemplo nao executa
        self.assertTrue(w.start_session())
        self.run_to_end()
        self.assertEqual(w.status, SessionStatus.STOPPED)
        events = [row[2] for row in w.events.model.rows]
        self.assertIn("paused", events)
        self.assertIn("resumed", events)
        self.assertEqual(w.capabilities.status_of(ActionKind.MOVE_TO), "Nao suportada")
        self.assertTrue(w.character.labels["quest_dialog"].text().startswith("desconhecido"))  # ultimo estado: null
        self.assertIn("Replay", w.status_label.text())
        self.assertIn("Parado", w.status_label.text())

    def test_manual_intervention_button(self):
        w = self.win
        replay = Path(self.tmp.name) / "q.jsonl"
        replay.write_text("\n".join(['{"quest_dialog": true}'] * 400) + "\n", encoding="utf-8")
        w.editor.open_file(SCRIPTS / "quest_watch.py")
        w.set_source(SourceKind.REPLAY, replay)
        w.start_session()
        self.assertTrue(self.pump(lambda: w.manual_action.isEnabled()))
        self.assertTrue(w.banner.isVisible())
        self.assertIn("intervencao manual", w.banner.text())
        w.manual_action.trigger()
        self.assertTrue(self.pump(lambda: any(r[2] == "resumed" for r in w.events.model.rows)))
        resumed = [r for r in w.events.model.rows if r[2] == "resumed"][0]
        self.assertIn("operador", resumed[3])
        w.stop_session()
        self.run_to_end()

    def test_pause_resume_stop_buttons_with_simulation(self):
        w = self.win
        w.profile.values["geral"]["tick_interval_ms"] = 40  # ~3 s de patrulha: tempo para os botoes
        w.load_example("scripts/circular_patrol.py", SourceKind.MOCK, "fixtures/patrol_world.json")
        w.start_session()
        self.assertTrue(self.pump(lambda: w.last_state is not None and w.last_state.tick >= 3))
        self.assertTrue(w.actions_run["pause"].isEnabled())
        w.actions_run["pause"].trigger()
        self.assertTrue(self.pump(lambda: w.status is SessionStatus.PAUSED))
        self.assertTrue(w.actions_run["resume"].isEnabled())
        self.assertFalse(w.manual_action.isEnabled())  # pausa do operador nao e de missao
        tick = w.last_state.tick
        self.pump(lambda: False, timeout=0.1)
        self.assertEqual(w.last_state.tick, tick)
        w.actions_run["resume"].trigger()
        self.assertTrue(self.pump(lambda: w.last_state.tick > tick))
        w.actions_run["stop"].trigger()
        self.run_to_end()
        self.assertEqual(w.status, SessionStatus.STOPPED)
        self.assertGreater(len(w.map.trail), 3)
        self.assertEqual(w.capabilities.status_of(ActionKind.MOVE_TO), "Simulacao")
        self.assertEqual(w.capabilities.status_of(ActionKind.ATTACK), "Nao suportada")

    def test_follow_simulation_updates_entities_and_uses_profile(self):
        w = self.win
        w.profile.set("grupo", "follow_distance", 300)
        w.load_example("scripts/follow_sarynne.py", SourceKind.MOCK, "fixtures/follow_world.json")
        w.start_session()
        self.run_to_end()
        self.assertEqual(w.session.params, {"leader": "Sarynne", "distance": 300})
        self.assertEqual(w.players.item(0, 0).text(), "Sarynne")
        self.assertEqual(w.npcs.message, "lista desconhecida (a fonte nao fornece)")

    def test_editor_save_run_and_error_line(self):
        w = self.win
        path = Path(self.tmp.name) / "meu.py"
        w.editor.new()
        w.editor.editor.setPlainText("def on_tick(bot):\n    x = 1\n    raise ValueError('falhou aqui')\n")
        self.assertTrue(w.editor.dirty)
        w.editor.save(path)
        self.assertFalse(w.editor.dirty)
        w.set_source(SourceKind.REPLAY, FIXTURES / "quest_dialog.jsonl")
        w.start_session()
        self.run_to_end()
        self.assertEqual(w.status, SessionStatus.FAILED)
        self.assertEqual(w.editor.editor.error_line, 3)
        self.assertIn("ValueError", w.editor.status.text())
        w.events.category.setCurrentIndex(2)  # Erros
        self.assertEqual(w.events.visible_events(), ["script_error"])

    def test_dirty_script_requires_confirmation_before_run(self):
        w = self.win
        path = Path(self.tmp.name) / "s.py"
        path.write_text("def on_tick(bot):\n    pass\n", encoding="utf-8")
        w.editor.open_file(path)
        w.editor.editor.setPlainText("def on_tick(bot):\n    bot.log('editado')\n")
        w.set_source(SourceKind.REPLAY, FIXTURES / "quest_dialog.jsonl")
        w.start_session()
        self.assertIn("Salvar script", self.answers)
        self.run_to_end()
        self.assertIn("editado", path.read_text(encoding="utf-8"))
        self.assertIn("editado", [r[3] for r in w.events.model.rows if r[2] == "script_log"][0])

    def test_live_source_is_disconnected_and_fails_explicitly(self):
        w = self.win
        w.editor.open_file(SCRIPTS / "quest_watch.py")
        w.set_source(SourceKind.LIVE, None)
        self.assertIn("nao implementado", w.source_label.text())
        self.assertIn("Desconectado", w.status_label.text())
        w.start_session()
        self.run_to_end()
        self.assertEqual(w.status, SessionStatus.FAILED)
        self.assertIn("nao implementado", w.editor.status.text())

    def test_layout_and_recent_paths_persist_without_autorun(self):
        w = self.win
        w.load_example("scripts/follow_sarynne.py", SourceKind.MOCK, "fixtures/follow_world.json")
        w.tabs.setCurrentIndex(1)
        w.close()
        again = self.new_window()
        self.assertEqual(again.editor.path, SCRIPTS / "follow_sarynne.py")
        self.assertEqual(again.source_kind, SourceKind.MOCK)
        self.assertEqual(again.tabs.currentIndex(), 1)
        self.assertIsNone(again.session)  # reabrir nunca executa

    def test_map_pick_sets_zone_center_in_profile(self):
        w = self.win
        w.map.resize(800, 600)
        p = w.map.pick(400, 300)
        w.set_zone_center(p)
        self.assertEqual(w.profile.get("zona", "center"), [p.x, p.y, p.z])
        self.assertIsNotNone(w.map.zone)
        self.assertTrue((self.cfg / "profile.json").is_file())


class PluginPanelTest(GuiTestCase):
    def test_install_enable_run_disable_remove(self):
        w = self.win
        panel = w.plugin_panel
        self.assertTrue(panel.install(str(ROOT / "plugins" / "event_counter")))
        self.assertIn("Instalar plugin", self.answers)
        self.assertIn("desativado", panel.list.item(0).text())
        self.assertTrue(panel.enable("event_counter"))
        self.assertIn("ativo", panel.list.item(0).text())
        w.load_example("scripts/quest_watch.py", SourceKind.REPLAY, "fixtures/quest_dialog.jsonl")
        w.start_session()
        self.run_to_end()
        logs = [r[3] for r in w.events.model.rows if r[2] == "plugin_log"]
        self.assertTrue(any("resumo:" in m for m in logs), logs)
        panel.disable("event_counter")
        self.assertIn("desativado", panel.list.item(0).text())
        self.assertTrue(panel.remove("event_counter"))
        self.assertEqual(panel.list.count(), 0)


class ConfigDialogTest(GuiTestCase):
    def test_sections_status_edit_diff_and_import_export(self):
        from l2native.gui.config import ConfigDialog
        w = self.win
        dlg = ConfigDialog(w.profile, "mock", frozenset({ActionKind.MOVE_TO}), parent=w)
        from l2native.profile import SECTIONS
        self.assertEqual(set(dlg.page_index), {s.key for s in SECTIONS})
        status = dlg.pages.widget(dlg.page_index["ataque"]).findChild(type(dlg.error), "status_ataque")
        self.assertIn("Nao suportada", status.text())
        status = dlg.pages.widget(dlg.page_index["grupo"]).findChild(type(dlg.error), "status_grupo")
        self.assertIn("Simulacao", status.text())
        self.assertFalse(dlg.fields["grupo"]["quest_dialogs"].widget.isEnabled())   # missoes manuais
        self.assertFalse(dlg.fields["geral"]["autostart"].widget.isEnabled())

        dlg.fields["cura.relaxar"]["sit_hp_below"].widget.setValue(33)
        dlg.rule_tables["cura"].add(Rule(RuleKind.SKILL, "Cura", priority=90, conditions=(
            Condition(ConditionField.HP_PCT, Operator.LT, 50),)))
        self.assertEqual(dlg.rule_tables["cura"].table.item(0, 9).text(), "sem estado")
        changes = dlg.changes()
        self.assertTrue(any("Sentar com HP" in c and "33" in c for c in changes), changes)

        out = Path(self.tmp.name) / "perfil.json"
        dlg.export_to(out)
        dlg.fields["grupo"]["follow_distance"].widget.setValue(999)
        dlg.import_from(out)
        self.assertEqual(dlg.fields["grupo"]["follow_distance"].widget.value(), 250)
        self.assertEqual(dlg.fields["cura.relaxar"]["sit_hp_below"].widget.value(), 33)

        dlg.fields["zona"]["center"].widget.setText("1, 2")
        dlg.accept()
        self.assertIn("posicao", dlg.error.text())
        dlg.fields["zona"]["center"].widget.setText("1, 2, 3")
        dlg.accept()
        w.apply_profile(dlg.result_profile)
        self.assertEqual(w.profile.get("zona", "center"), [1.0, 2.0, 3.0])
        self.assertEqual(len(w.profile.rules["cura"]), 1)

    def test_rule_now_column_uses_last_state(self):
        from l2native.gui.config import RuleTable
        from l2native.model import GameState, observed
        state = GameState(tick=1, hp=observed(0.3, "t"))
        t = RuleTable([Rule(RuleKind.SKILL, "Cura", conditions=(Condition(ConditionField.HP_PCT, Operator.LT, 50),)),
                       Rule(RuleKind.SKILL, "Combate", conditions=(
                           Condition(ConditionField.IN_COMBAT, Operator.EQ, True),))], lambda: state)
        self.assertEqual(t.table.item(0, 9).text(), "verdadeira")
        self.assertEqual(t.table.item(1, 9).text(), "desconhecida")


if __name__ == "__main__":
    unittest.main()
