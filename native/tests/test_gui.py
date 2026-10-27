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
        params = dict(w.session.params)
        self.assertEqual(params.pop("profile")["values"]["grupo"]["follow_distance"], 300)
        self.assertEqual(params, {"leader": "Sarynne", "distance": 300})
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


class ComboEnumTest(GuiTestCase):
    """Regressao (Windows, PySide6 6.12): enums que herdam de str voltavam do QComboBox
    como str puro ('str' object has no attribute 'label' / 'type'). Agora os testes
    passam pelo proprio combo, como o operador."""

    def test_source_combo_by_user_keeps_source_kind(self):
        w = self.win
        for i, kind in enumerate(SourceKind):
            w.source_combo.setCurrentIndex(i)
            self.assertIs(w.source_kind, kind)
            self.assertIn(f"Fonte: {kind.label}", w.status_label.text())
        w.set_source(SourceKind.MOCK, None)
        self.assertEqual(w.source_combo.currentIndex(), list(SourceKind).index(SourceKind.MOCK))

    def test_rule_dialog_field_combo_for_every_field(self):
        from l2native.gui.config import RuleDialog
        from l2native.rules import FieldType
        dlg = RuleDialog(parent=self.win)
        for i, f in enumerate(ConditionField):
            dlg.c_field.setCurrentIndex(i)
            self.assertEqual(dlg.c_op.count(), 2 if f.type is FieldType.BOOL else len(Operator))
            dlg.c_value.setText("sim" if f.type is FieldType.BOOL else "10")
            dlg._add_condition()
            self.assertEqual(dlg.error.text(), "")
            self.assertIs(dlg.conditions[-1].field, f)


class ConfigDialogTest(GuiTestCase):
    def test_sections_status_edit_diff_and_import_export(self):
        from l2native.gui.config import ConfigDialog
        w = self.win
        dlg = ConfigDialog(w.profile, "mock", frozenset({ActionKind.MOVE_TO}), parent=w)
        from l2native.gui.config import SKILLS_PAGE
        from l2native.profile import SECTIONS
        self.assertEqual(set(dlg.page_index), {s.key for s in SECTIONS} | {SKILLS_PAGE})
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


class Registro022GuiTest(GuiTestCase):
    """Experimento 023: catalogo de habilidades, decisoes sem execucao, catalogo de
    entidades, prontidao de observacao, camadas do mapa e editor de rota."""

    def test_skill_catalog_page_feeds_rule_id_column(self):
        from l2native.gui.config import ConfigDialog
        from l2native.profile import SkillEntry
        dlg = ConfigDialog(self.win.profile, "mock", frozenset({ActionKind.MOVE_TO}), parent=self.win)
        cura = dlg.rule_tables["cura"]
        cura.add(Rule(RuleKind.SKILL, "Cura"))
        self.assertEqual(cura.table.item(0, 7).text(), "nao catalogada")
        dlg.skill_table.put(SkillEntry("Cura", 7, False))
        self.assertEqual(cura.table.item(0, 7).text(), "7 (nao confirmado)")
        dlg.skill_table.name.setText("cura")
        dlg.skill_table.game_id.setText("7")
        dlg.skill_table.confirmed.setChecked(True)
        dlg.skill_table._add_from_fields()
        self.assertEqual(len(dlg.skill_table.skills), 1)              # mesmo nome: substitui
        self.assertEqual(cura.table.item(0, 7).text(), "7")
        dlg.skill_table.name.setText("Outra")
        dlg.skill_table.game_id.setText("")
        dlg.skill_table._add_from_fields()                            # confirmada sem ID: recusada
        self.assertIn("sem ID", dlg.skill_table.error.text())
        self.assertTrue(any("Catalogo" in c for c in dlg.changes()))
        dlg.accept()
        self.assertEqual(dlg.result_profile.resolve_game_id(Rule(RuleKind.SKILL, "Cura")), 7)
        dlg.import_from(FIXTURES / "profiles" / "exemplo_v1.json")
        self.assertIn("v1->v2", dlg.info.text())
        self.assertEqual({s.name for s in dlg.skill_table.skills}, {"Cura Leve", "Golpe Forte"})

    def test_v1_profile_on_disk_is_migrated_with_backup(self):
        self.win.close()
        (self.cfg / "profile.json").write_bytes((FIXTURES / "profiles" / "exemplo_v1.json").read_bytes())
        w = self.new_window()
        self.assertIn("v1->v2", w.statusBar().currentMessage())
        self.assertTrue((self.cfg / "profile.v1.json").is_file())
        self.assertIn("l2tools-profile/2", (self.cfg / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(w.profile.skill("Golpe Forte").game_id, 900002)

    def test_decision_panel_marks_chosen_rule_without_executing(self):
        from l2native.gui.panels import RuleDecisionPanel
        from l2native.model import GameState, observed
        from l2native.profile import Profile
        profile = Profile.load(FIXTURES / "profiles" / "exemplo_v2.json")
        panel = RuleDecisionPanel()
        panel.set_profile(profile)
        panel.update_state(GameState(tick=1, hp=observed(0.3, "t"), in_combat=observed(True, "t"),
                                     target=observed("Lobo", "t")))
        rows = dict(zip(panel.column(1), panel.column(4)))
        self.assertEqual(rows["Cura Leve"], RuleDecisionPanel.CHOSEN)
        self.assertEqual(rows["Golpe Forte"], RuleDecisionPanel.CHOSEN)
        self.assertEqual(rows["Golpe Basico"], "sem ID confirmado")
        panel.update_state(GameState(tick=2, hp=observed(0.3, "t"), in_combat=observed(True, "t"),
                                     target=observed("Lobo", "t")))
        self.assertEqual(dict(zip(panel.column(1), panel.column(4)))["Cura Leve"], "em intervalo")
        panel.update_state(GameState(tick=3))
        self.assertEqual(set(panel.column(4)), {"condicao desconhecida"})

    def test_route_example_feeds_catalog_observation_and_map(self):
        from l2native.observation import Requirement
        w = self.win
        w.load_example("scripts/route_walker.py", SourceKind.MOCK, "fixtures/route_world.json")
        self.assertEqual(len(w.map.route.nodes), 7)
        w.start_session()
        self.run_to_end()
        self.assertIn("destino alcancado", w.session.report.reason)
        self.assertIn("declara", w.observation.declared_label.text())
        self.assertEqual(w.observation.status_of(Requirement.SELF_POSITION), "atendido")
        self.assertEqual(w.observation.status_of(Requirement.VITALS), "nao atendido")
        self.assertEqual(w.catalog.rowCount(), 0)                       # o simulado de rota nao tem entidades
        w.load_example("scripts/follow_sarynne.py", SourceKind.MOCK, "fixtures/follow_world.json")
        w.start_session()
        self.run_to_end()
        self.assertEqual(w.catalog.column(1), ["Sarynne"])
        self.assertGreater(int(w.catalog.item(0, 5).text()), 1)

    def test_layers_and_route_editor(self):
        w = self.win
        w.layer_actions["grade"].setChecked(False)
        w.layer_actions["trilha"].setChecked(False)
        self.assertFalse(w.map.layers["grade"] or w.map.layers["trilha"])
        w.map.resize(800, 600)
        w.new_route()
        self.assertTrue(w.route_edit_action.isChecked())
        for vx, vy in ((100, 100), (300, 100), (300, 300)):
            w.map.pick(vx, vy)
        self.assertEqual(sorted(w.route.nodes), [1, 2, 3])
        self.assertEqual(w.route.shortest_path(1, 3), [1, 2, 3])
        w.undo_route_node()
        self.assertEqual(sorted(w.route.nodes), [1, 2])
        self.assertEqual(w.route_last, 2)
        w.map.grab()                                                    # desenha com camadas e rota
        w.route_edit_action.setChecked(False)
        w.map.pick(500, 500)
        self.assertEqual(len(w.route.nodes), 2)                         # fora do modo de edicao nao adiciona
        out = self.cfg / "minha_rota.json"
        w.save_route(out)
        self.assertEqual(w.profile.get("rota", "route_file"), str(out))
        self.assertFalse(w.route_dirty)
        self.assertTrue(w.load_route(FIXTURES / "route_demo.json"))
        self.assertEqual(w.show_shortest_path(1, 7), [1, 5, 4, 7])
        self.assertEqual(w.map.route_path, [1, 5, 4, 7])
        self.assertIn("1366", w.statusBar().currentMessage())
        w.map.grab()


class Registro021PoliciesGuiTest(GuiTestCase):
    def test_hunter_example_runs_policies_in_simulation(self):
        from l2native.gui.config import ConfigDialog, section_status
        from l2native.profile import SECTION_BY_KEY
        w = self.win
        w.profile.save(w.profile_path)
        w.load_example("scripts/hunter.py", SourceKind.MOCK, "fixtures/hunt_world.json")
        self.assertIn("Perfil do exemplo", self.answers)
        self.assertTrue((self.cfg / "profile.anterior.json").is_file())
        self.assertEqual(w.profile.name, "cacador-simulado")
        w.profile.values["geral"]["tick_interval_ms"] = 5      # o perfil do exemplo usa 200 ms
        w.start_session()
        self.run_to_end()
        self.assertIn("3 alvos derrotados", w.session.report.reason)
        self.assertEqual(w.capabilities.status_of(ActionKind.REST), "Simulacao")
        self.assertEqual(w.capabilities.status_of(ActionKind.ATTACK), "Simulacao")
        self.assertEqual(w.catalog.rowCount(), 4)      # NPCs por tipo (Lobo, Guarda, Urso) + a jogadora Andarilha
        logs = [r[3] for r in w.events.model.rows if r[2] == "script_log"]
        self.assertTrue(any("relaxar: sentar" in m for m in logs))
        caps = w.capabilities.capabilities
        self.assertIn("Simulacao", section_status(SECTION_BY_KEY["cura.relaxar"], "mock", caps))
        dlg = ConfigDialog(w.profile, "mock", caps, parent=w)
        self.assertEqual(dlg.rule_tables["ataque"].table.item(0, 7).text(), "900002")
        w.map.grab()


class LiveIdentSessionsGuiTest(GuiTestCase):
    """LIVE-IDENT-001: janelas candidatas, vinculo manual e estados separados."""

    def make(self, windows, ask_binding=None, ask_rule=None):
        from l2native.discovery import StaticWindowSource
        self.win.close()
        self.source = StaticWindowSource(windows)
        win = self.MainWindow(config_path=self.cfg, confirm=self._confirm, window_source=self.source,
                              sessions_kwargs={"ask_binding": ask_binding, "ask_rule": ask_rule})
        win.show()
        self.win = win
        return win

    def test_rule_from_observed_window_bind_persist_and_close(self):
        from l2native.discovery import IDENTITY_UNOBSERVED, CandidateRule, LocalIdentity, WindowInfo
        client = WindowInfo(1001, 40, "Cliente A", "ClasseCliente", "cliente.exe")
        other = WindowInfo(2001, 50, "notas.txt", "Notepad", "notepad.exe")
        w = self.make([client, other], ask_binding=lambda known, prof: LocalIdentity("Seguidora", prof),
                      ask_rule=lambda win: CandidateRule.from_window(win, "cliente", "vi na minha tela"))
        p = w.sessions
        self.assertEqual(p.table.rowCount(), 0)
        self.assertIn("Nenhuma regra", p.message.text())
        self.assertIn("nao prova personagem logado", p.findChildren(type(p.message))[-1].text())
        p.show_all.setChecked(True)
        self.assertEqual(p.table.rowCount(), 2)
        row = [r for r in range(2) if p.table.item(r, 0).text() == "cliente.exe"][0]
        p.select_row(row)
        p.bind_dialog()                                                     # sem regra: recusado
        self.assertIn("so janelas candidatas", p.message.text())
        p.rule_dialog()
        p.show_all.setChecked(False)
        self.assertEqual(p.table.rowCount(), 1)
        self.assertEqual(p.table.item(0, 4).text(), "localizada (regra: cliente)")
        p.select_row(0)
        p.bind_dialog()
        self.assertIn("Seguidora / perfil padrao", p.table.item(0, 5).text())
        self.assertEqual(p.table.item(0, 6).text(), IDENTITY_UNOBSERVED)
        self.assertIn("LiveAdapter nao implementado", p.table.item(0, 9).text())
        self.assertIsNone(w.session)                                        # nada executou
        saved = (self.cfg / "discovery.json").read_text(encoding="utf-8")
        self.assertIn("cliente.exe", saved)
        self.assertIn("Seguidora", saved)
        self.assertNotIn("1001", saved)                                     # HWND/PID nao persistem
        self.source.windows.remove(client)                                  # cliente fechado
        p.refresh()
        self.assertEqual(p.table.rowCount(), 0)
        self.assertIn("Nenhuma janela candidata", p.message.text())
        self.assertIn("session_closed", [r[2] for r in w.events.model.rows])
        self.source.windows.append(client)
        again = self.make([client])                                         # reinicio: regra volta, vinculo nao
        self.assertEqual(again.sessions.table.rowCount(), 1)
        self.assertEqual(again.sessions.table.item(0, 5).text(), "nao associado")
        self.assertEqual([i.label for i in again.discovery.config.identities], ["Seguidora"])
        again.sessions.select_row(0)
        again.sessions.unbind_selected()
        again.sessions.remove_rule()
        self.assertEqual(again.discovery.config.rules, [])

    def test_auto_refresh_and_live_source_hint(self):
        from l2native.discovery import CandidateRule, DiscoveryConfig, WindowInfo
        self.cfg.mkdir(parents=True, exist_ok=True)
        DiscoveryConfig([CandidateRule("c", process="cliente.exe")]).save(self.cfg / "discovery.json")
        w = self.make([])
        w.sessions.timer.setInterval(20)
        w.sessions.auto.setChecked(True)
        self.source.windows.append(WindowInfo(7, 8, "Cliente", "X", "cliente.exe"))
        self.assertTrue(self.pump(lambda: w.sessions.table.rowCount() == 1, timeout=3))
        w.sessions.auto.setChecked(False)
        w.set_source(SourceKind.LIVE, None)
        self.assertIn("Sessoes locais", w.source_label.text())



class IdentityGuiTest(GuiTestCase):
    """Experimento 028: identificacao visual no painel de sessoes (OCR e frames substituiveis)."""

    def setUp(self):
        super().setUp()
        from .test_identity import np
        if np is None:
            self.skipTest("numpy/OpenCV ausentes")

    def make(self, windows, *, region=True, confirm=None, probe_options=None, ocr_factory=None, ask_region=None):
        from l2native.discovery import CandidateRule, DiscoveryConfig, StaticWindowSource
        from l2native.ocr import ScriptedOcr
        from l2native.vision.frames import StaticFrameSource
        from .test_identity import CLIENT, ROI, render_client
        self.win.close()
        self.cfg.mkdir(parents=True, exist_ok=True)
        rule = CandidateRule("cliente", process="L2.bin", evidence="teste")
        DiscoveryConfig([rule], regions={"cliente": ROI} if region else {}).save(self.cfg / "discovery.json")
        self.source = StaticWindowSource(windows)
        self.answer = [("Seratonina", 0.95)]
        self.grabbed = []

        def frames(w):
            self.grabbed.append(w.hwnd)
            return StaticFrameSource([render_client("x")] * 5, client_size=CLIENT)
        kwargs = {"ocr_factory": ocr_factory or (lambda: ScriptedOcr([self.answer])),
                  "frame_source_factory": frames,
                  "probe_options": {"samples": 2, "interval_s": 0.0, **(probe_options or {})},
                  "ask_region": ask_region}
        if confirm is not None:
            kwargs["confirm"] = confirm
        win = self.MainWindow(config_path=self.cfg, confirm=self._confirm, window_source=self.source,
                              sessions_kwargs=kwargs)
        win.show()
        self.win = win
        self.addCleanup(win.sessions.shutdown)
        return win.sessions

    def windows(self):
        from l2native.discovery import WindowInfo
        a = WindowInfo(1001, 40, "Lineage II", "L2UnrealWWindowsViewportWindow", "L2.bin", started_at=500.0)
        b = WindowInfo(1002, 41, "Lineage II", "L2UnrealWWindowsViewportWindow", "L2.bin", started_at=510.0)
        return a, b

    def identify(self, p, row):
        p.select_row(row)
        p.buttons["identify"].click()
        self.assertTrue(self.pump(lambda: p.job is None, timeout=5), p.message.text())

    def test_button_reads_and_result_lands_on_the_right_row(self):
        from l2native.discovery import LocalIdentity
        a, b = self.windows()
        p = self.make([a, b])
        self.assertEqual(p.table.rowCount(), 2)
        self.assertIn("ainda nao identificada", p.table.item(0, p.COL_IDENTITY).text())
        p.bind(p.rows[0], LocalIdentity("Seratonina"))
        p.bind(p.rows[1], LocalIdentity("Velina"))
        row_b = p.row_of(self.win.discovery.sessions[b.key].session_id)
        self.answer = [("Velina", 0.95)]
        self.identify(p, row_b)
        self.assertEqual(self.grabbed, [b.hwnd])                       # so a janela selecionada
        self.assertIn("compativel", p.table.item(row_b, p.COL_IDENTITY).text())
        self.assertEqual(p.table.item(row_b, p.COL_CONFIDENCE).text(), "0.95")
        row_a = 1 - row_b
        self.assertIn("ainda nao identificada", p.table.item(row_a, p.COL_IDENTITY).text())
        self.answer = [("OutraPersonagem", 0.95)]
        self.identify(p, row_a)
        self.assertIn("DIVERGENTE", p.table.item(row_a, p.COL_IDENTITY).text())
        self.assertIn("DIFERENTE do perfil", p.message.text())
        self.assertFalse(self.win.discovery.action_gate(a.key)[0])
        self.assertIsNone(self.win.session)                             # nenhum script iniciou
        self.assertIn("LiveAdapter nao implementado", p.table.item(row_a, 9).text())
        events = [(r[2], r[3]) for r in self.win.events.model.rows if r[2] == "identity_observed"]
        self.assertEqual(len(events), 2)
        self.assertFalse(any("OutraPersonagem" in d or "Velina" in d for _, d in events))   # sem o texto lido
        p.select_row(row_a)
        p.buttons["clear"].click()
        self.assertIn("ainda nao identificada", p.table.item(row_a, p.COL_IDENTITY).text())

    def test_worker_keeps_gui_responsive_and_cancel_works(self):
        import time as _time
        from PySide6.QtCore import QTimer
        a, _ = self.windows()
        p = self.make([a], probe_options={"samples": 3, "interval_s": 30.0})
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        self.addCleanup(timer.stop)
        p.select_row(0)
        started = _time.monotonic()
        self.assertTrue(p.identify_selected())
        self.assertLess(_time.monotonic() - started, 0.5)               # nao esperou a leitura
        self.assertFalse(p.buttons["identify"].isEnabled())
        self.assertEqual(p.table.item(0, p.COL_IDENTITY).text(), "identificando…")
        self.assertTrue(self.pump(lambda: len(ticks) >= 5, timeout=3))   # a GUI segue processando eventos
        self.assertIsNotNone(p.job)                                      # ainda esperando a 2a amostra
        cancelled_at = _time.monotonic()
        p.buttons["cancel"].click()
        self.assertTrue(self.pump(lambda: p.job is None, timeout=3))
        self.assertLess(_time.monotonic() - cancelled_at, 2.0)
        self.assertIn("CANCELADA", p.table.item(0, p.COL_IDENTITY).text())
        self.assertTrue(p.buttons["identify"].isEnabled())

    def test_ocr_failure_and_missing_region_show_reason(self):
        from l2native.ocr import OcrUnavailable
        a, _ = self.windows()

        def missing():
            raise OcrUnavailable("TESSERACT_AUSENTE: programa tesseract nao encontrado")
        p = self.make([a], ocr_factory=missing)
        self.identify(p, 0)
        self.assertIn("TESSERACT_AUSENTE", p.message.text())
        self.assertIn("indisponivel: TESSERACT_AUSENTE", p.table.item(0, p.COL_IDENTITY).text())
        p = self.make([a], region=False)
        self.identify(p, 0)
        self.assertIn("REGIAO_NAO_CONFIGURADA", p.table.item(0, p.COL_IDENTITY).text())

    def test_window_change_discards_pending_result(self):
        import threading
        from l2native.discovery import WindowInfo
        from l2native.ocr import ScriptedOcr
        a, _ = self.windows()
        gate = threading.Event()

        class Slow(ScriptedOcr):
            def recognize(self, image):
                gate.wait(5)
                return super().recognize(image)
        p = self.make([a], ocr_factory=lambda: Slow([[("Seratonina", 0.95)]]))
        p.select_row(0)
        p.identify_selected()
        self.source.windows[0] = WindowInfo(1001, 40, "Lineage II", "L2UnrealWWindowsViewportWindow", "L2.bin",
                                            started_at=900.0)             # cliente reaberto: mesmo HWND/PID
        p.refresh()
        gate.set()
        self.assertTrue(self.pump(lambda: p.job is None, timeout=5))
        self.assertIn("Resultado descartado", p.message.text())
        self.assertIn("ainda nao identificada", p.table.item(0, p.COL_IDENTITY).text())

    def test_region_configuration_and_dialog(self):
        from PySide6.QtCore import QRect
        from l2native.gui.sessions import RegionDialog
        from l2native.vision.frames import Frame, RegionOfInterest
        from .test_identity import CLIENT, render_client
        a, _ = self.windows()
        asked = []

        def ask(frame):
            asked.append(frame.client_size)
            return RegionOfInterest(0.1, 0.1, 0.2, 0.05, frame.client_size)
        p = self.make([a], region=False, ask_region=ask)
        p.select_row(0)
        p.buttons["region"].click()
        self.assertEqual(asked, [CLIENT])
        saved = (self.cfg / "discovery.json").read_text(encoding="utf-8")
        self.assertIn("identity_regions", saved)
        self.assertNotIn("1001", saved)
        self.assertIn("Regiao do nome salva", p.message.text())
        dlg = RegionDialog(Frame(render_client("x"), CLIENT, 0.0))
        self.assertIsNone(dlg.region())
        dlg.set_selection(QRect(0, 0, dlg.view.width() // 4, dlg.view.height() // 10))
        roi = dlg.region()
        self.assertEqual((round(roi.w, 2), round(roi.h, 2), roi.reference_size), (0.25, 0.1, CLIENT))
        dlg.set_selection(QRect(0, 0, 2, 2))
        self.assertIsNone(dlg.region())                                  # pequena demais

    def test_suggested_rule_needs_confirmation(self):
        from l2native.discovery import WindowInfo
        client = WindowInfo(3001, 70, "Lineage II", "L2UnrealWWindowsViewportWindow", "L2.bin")
        answers = []
        p = self.make([client], confirm=lambda title, text: answers.append(text) or False)
        self.win.discovery.config.rules.clear()
        p.refresh()
        self.assertEqual(p.table.rowCount(), 0)
        self.assertIn("regra sugerida", p.message.text())
        p.buttons["suggested"].click()
        self.assertIn("E13", answers[0])
        self.assertEqual(self.win.discovery.config.rules, [])
        p.confirm = lambda title, text: True
        p.buttons["suggested"].click()
        rule = self.win.discovery.config.rules[0]
        self.assertIn("confirmada pelo operador", rule.origin)
        self.assertEqual(p.table.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
