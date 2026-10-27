"""Janela principal (workspace): mapa/editor no centro, paineis acoplaveis em volta."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDockWidget, QFileDialog, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QTabWidget, QToolBar, QVBoxLayout, QWidget)

from ..actions import ActionKind
from ..adapters.factory import SourceKind
from ..adapters.replay import state_from_dict
from ..engine import EngineConfig
from ..journal import Record
from ..model import GameState, Position
from ..plugins import PluginManager
from ..profile import Profile
from ..session import Session, SessionStatus
from .config import ConfigDialog
from .editor import ScriptEditor
from .mapview import MapView
from .panels import CapabilityPanel, CharacterPanel, EntityTable, EventLog, InfoPanel, PluginPanel, SessionBridge

NATIVE_DIR = Path(__file__).resolve().parents[2]
EXAMPLES = (
    ("Missao manual (replay)", "scripts/quest_watch.py", SourceKind.REPLAY, "fixtures/quest_dialog.jsonl"),
    ("Seguir Sarynne (replay)", "scripts/follow_sarynne.py", SourceKind.REPLAY, "fixtures/follow_sarynne.jsonl"),
    ("Seguir Sarynne (simulado)", "scripts/follow_sarynne.py", SourceKind.MOCK, "fixtures/follow_world.json"),
    ("Patrulha circular (simulado)", "scripts/circular_patrol.py", SourceKind.MOCK, "fixtures/patrol_world.json"),
)
STATUS_TEXT = {
    None: "Ocioso", SessionStatus.IDLE: "Ocioso", SessionStatus.RUNNING: "Executando", SessionStatus.PAUSED: "Pausado",
    SessionStatus.STOPPING: "Parando…", SessionStatus.STOPPED: "Parado", SessionStatus.FAILED: "Falhou",
}


def config_dir() -> Path:
    override = os.environ.get("L2TOOLS_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "l2tools"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "l2tools"


Confirm = Callable[[str, str], bool]


class MainWindow(QMainWindow):
    def __init__(self, config_path: Optional[Path] = None, confirm: Optional[Confirm] = None) -> None:
        super().__init__()
        self.setWindowTitle("L2-Tools Native")
        self.resize(1400, 860)
        self.cfg_dir = Path(config_path) if config_path else config_dir()
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self.settings = QSettings(str(self.cfg_dir / "gui.ini"), QSettings.Format.IniFormat)
        self.confirm: Confirm = confirm or self._ask
        self.profile_path = self.cfg_dir / "profile.json"
        self.profile = self._load_profile()
        self.session: Optional[Session] = None
        self.source_kind = SourceKind.REPLAY
        self.source_path: Optional[Path] = None
        self.last_state: Optional[GameState] = None
        self.pause_reason: Optional[dict] = None

        # centro: Mapa <-> Script, com faixa de pausa acima
        self.map = MapView()
        self.editor = ScriptEditor()
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.South)
        self.tabs.addTab(self.map, "Mapa")
        self.tabs.addTab(self.editor, "Script")
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background:#fff3cd; color:#5c4400; padding:6px; border:1px solid #e0c36c;")
        self.banner.hide()
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.banner)
        cl.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        # paineis
        self.character = CharacterPanel()
        self.players = EntityTable("characters")
        self.npcs = EntityTable("npcs")
        self.target = InfoPanel("Alvo: desconhecido")
        self.drops = InfoPanel("Drops: nenhuma fonte de dados de itens no chao nos adaptadores atuais (desconhecido).")
        self.capabilities = CapabilityPanel()
        self.events = EventLog()
        self.plugins = PluginManager(self.cfg_dir / "plugins", notify=self._plugin_event)
        self.plugin_panel = PluginPanel(self.plugins, self.confirm, self._choose_plugin_folder)
        self.docks: dict[str, QDockWidget] = {}
        L, R, B = (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea,
                   Qt.DockWidgetArea.BottomDockWidgetArea)
        for key, title, widget, area in (
                ("character", "Sessao / Personagem", self.character, L), ("plugins", "Plugins", self.plugin_panel, L),
                ("target", "Alvo", self.target, R), ("players", "Jogadores", self.players, R),
                ("npcs", "NPCs", self.npcs, R), ("drops", "Drops", self.drops, R),
                ("capabilities", "Capacidades do adaptador", self.capabilities, R),
                ("events", "Eventos", self.events, B)):
            dock = QDockWidget(title, self)
            dock.setObjectName(f"dock_{key}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.docks[key] = dock
        self.tabifyDockWidget(self.docks["players"], self.docks["npcs"])
        self.tabifyDockWidget(self.docks["npcs"], self.docks["drops"])
        self.docks["players"].raise_()

        self.bridge = SessionBridge()
        self.bridge.record.connect(self.on_record)
        self.bridge.status.connect(self.on_status)
        self._build_toolbar()
        self._build_menus()
        self.editor.newRequested.connect(self.new_script)
        self.editor.openRequested.connect(self.open_script_dialog)
        self.editor.saveRequested.connect(self.save_script)
        self.editor.saveAsRequested.connect(self.save_script_as)
        self.editor.runRequested.connect(self.start_session)
        self.map.zoneCenterRequested.connect(self.set_zone_center)
        self.map.pointPicked.connect(lambda p: self.statusBar().showMessage(f"Ponto: {p.x:.0f}, {p.y:.0f}, {p.z:.0f}", 5000))
        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)
        self._default_layout = self.saveState()
        self._restore()
        self._apply_profile_to_views()
        self._refresh_controls()

    # --- utilitarios ---------------------------------------------------------
    def _ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def _choose_plugin_folder(self) -> Optional[str]:
        return QFileDialog.getExistingDirectory(self, "Pasta do plugin (com plugin.json)", str(NATIVE_DIR / "plugins")) or None

    def _load_profile(self) -> Profile:
        if self.profile_path.is_file():
            try:
                return Profile.load(self.profile_path)
            except (OSError, ValueError, KeyError):
                pass
        return Profile.default()

    def _plugin_event(self, event: str, data: dict) -> None:
        self.events.add(Record(0, 0, event, data))

    # --- barra de ferramentas e menus ---------------------------------------------
    def _build_toolbar(self) -> None:
        tb = QToolBar("Execucao", self)
        tb.setObjectName("toolbar_run")
        self.addToolBar(tb)
        self.source_combo = QComboBox()
        for kind in SourceKind:
            self.source_combo.addItem(kind.label, kind)
        self.source_combo.currentIndexChanged.connect(lambda _: self.set_source(self.source_combo.currentData(), None))
        tb.addWidget(QLabel(" Fonte: "))
        tb.addWidget(self.source_combo)
        self.source_button = QPushButton("Escolher arquivo…")
        self.source_button.clicked.connect(self.choose_source_dialog)
        tb.addWidget(self.source_button)
        self.source_label = QLabel()
        tb.addWidget(self.source_label)
        tb.addSeparator()
        self.actions_run: dict[str, QAction] = {}
        for key, text, shortcut, fn in (("start", "▶ Iniciar", "F5", self.start_session),
                                        ("pause", "⏸ Pausar", "F6", self.pause_session),
                                        ("resume", "⏵ Retomar", "F7", self.resume_session),
                                        ("stop", "■ Parar", "Shift+F5", self.stop_session)):
            act = QAction(text, self)
            act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(fn)
            tb.addAction(act)
            self.actions_run[key] = act
        tb.addSeparator()
        self.manual_action = QAction("✔ Intervencao manual concluida", self)
        self.manual_action.setToolTip("Confirme depois de aceitar/entregar a missao manualmente no jogo")
        self.manual_action.triggered.connect(self.confirm_manual_intervention)
        tb.addAction(self.manual_action)

    def _build_menus(self) -> None:
        m = self.menuBar().addMenu("&Arquivo")
        m.addAction("Abrir replay…", lambda: self.choose_source_dialog(SourceKind.REPLAY))
        m.addAction("Abrir cenario simulado…", lambda: self.choose_source_dialog(SourceKind.MOCK))
        ex = m.addMenu("Exemplos")
        for title, script, kind, src in EXAMPLES:
            a = ex.addAction(title, lambda s=script, k=kind, f=src: self.load_example(s, k, f))
            a.setEnabled((NATIVE_DIR / script).is_file())
        m.addSeparator()
        m.addAction("Sair", self.close)
        s = self.menuBar().addMenu("&Script")
        s.addAction("Novo", self.new_script)
        s.addAction("Abrir…", self.open_script_dialog, QKeySequence.StandardKey.Open)
        s.addAction("Salvar", self.save_script, QKeySequence.StandardKey.Save)
        s.addAction("Salvar como…", self.save_script_as)
        c = self.menuBar().addMenu("&Configuracoes")
        c.addAction("Perfil e politicas…", self.open_config)
        v = self.menuBar().addMenu("&Visualizar")
        for dock in self.docks.values():
            v.addAction(dock.toggleViewAction())
        v.addSeparator()
        v.addAction("Mapa", lambda: self.tabs.setCurrentWidget(self.map))
        v.addAction("Script", lambda: self.tabs.setCurrentWidget(self.editor))
        v.addAction("Restaurar layout padrao", lambda: self.restoreState(self._default_layout))

    # --- fonte e script -------------------------------------------------------------
    def set_source(self, kind: SourceKind, path: Optional[Path | str]) -> None:
        self.source_kind = kind
        self.source_path = Path(path) if path else None
        if self.source_combo.currentData() is not kind:
            self.source_combo.blockSignals(True)
            self.source_combo.setCurrentIndex(list(SourceKind).index(kind))
            self.source_combo.blockSignals(False)
        self.source_button.setEnabled(kind is not SourceKind.LIVE)
        if kind is SourceKind.LIVE:
            self.source_label.setText(" LiveAdapter nao implementado ")
        else:
            self.source_label.setText(f" {self.source_path.name if self.source_path else '(nenhum arquivo)'} ")
        self._refresh_controls()

    def choose_source_dialog(self, kind: Optional[SourceKind] = None) -> None:
        kind = kind or self.source_kind
        pattern = "Replay (*.jsonl)" if kind is SourceKind.REPLAY else "Cenario (*.json)"
        path, _ = QFileDialog.getOpenFileName(self, "Fonte de dados", str(NATIVE_DIR / "fixtures"), pattern)
        if path:
            self.set_source(kind, path)

    def load_example(self, script: str, kind: SourceKind, source: str) -> None:
        if not self._may_discard_script():
            return
        self.editor.open_file(NATIVE_DIR / script)
        self.set_source(kind, NATIVE_DIR / source)
        self.tabs.setCurrentWidget(self.map)

    def _may_discard_script(self) -> bool:
        return not self.editor.dirty or self.confirm("Script alterado", "Descartar alteracoes nao salvas?")

    def new_script(self) -> None:
        if self._may_discard_script():
            self.editor.new()
            self.tabs.setCurrentWidget(self.editor)

    def open_script_dialog(self) -> None:
        if not self._may_discard_script():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Abrir script Python", str(NATIVE_DIR / "scripts"), "Python (*.py)")
        if path:
            self.editor.open_file(path)
            self.tabs.setCurrentWidget(self.editor)

    def save_script(self) -> bool:
        if self.editor.path is None:
            return self.save_script_as()
        self.editor.save()
        return True

    def save_script_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Salvar script", str(NATIVE_DIR / "scripts"), "Python (*.py)")
        if not path:
            return False
        self.editor.save(path)
        return True

    # --- sessao ----------------------------------------------------------------------
    @property
    def status(self) -> Optional[SessionStatus]:
        return self.session.status if self.session else None

    def start_session(self) -> bool:
        if self.session and not self.session.finished:
            return False
        if self.editor.dirty:
            if not self.confirm("Salvar script", "O script tem alteracoes. Salvar e executar a versao salva?"):
                return False
            if not self.save_script():
                return False
        if self.editor.path is None:
            QMessageBox.warning(self, "Sem script", "Abra ou salve um script .py antes de iniciar.")
            return False
        g = self.profile.values["geral"]
        config = EngineConfig(tick_interval=g["tick_interval_ms"] / 1000, max_move_distance=g["max_move_distance"],
                              max_consecutive_script_errors=g["max_script_errors"])
        self.session = Session(self.source_kind, self.source_path, self.editor.path,
                               params=self.profile.script_params(), config=config)
        self.session.subscribe(self.bridge.listener)
        self.map.reset()
        self.character.clear()
        self.capabilities.set_adapter(None, frozenset())
        self.last_state = None
        self.pause_reason = None
        self.banner.hide()
        self.editor.editor.mark_error(None)
        self.character.set_session(f"{self.source_kind.label} · {self.editor.path.name}")
        ok = self.session.start()
        self._refresh_controls()
        return ok

    def pause_session(self) -> None:
        if self.session:
            self.session.pause()

    def resume_session(self) -> None:
        if self.session:
            self.session.resume()

    def confirm_manual_intervention(self) -> None:
        """O operador confirma que resolveu a missao no jogo; retoma de forma controlada."""
        if self.session and self.status is SessionStatus.PAUSED:
            self.session.resume()

    def stop_session(self) -> None:
        if self.session:
            self.session.stop()
            self._refresh_controls()

    # --- eventos do motor (thread Qt) ---------------------------------------------------
    def on_record(self, record: Record) -> None:
        self.events.add(record)
        self.plugins.dispatch(record)
        e, d = record.event, record.data
        if e == "observation":
            state = state_from_dict(d["state"])
            self.last_state = state
            self.character.update_state(state)
            self.players.update_state(state)
            self.npcs.update_state(state)
            self.target.setText(f"Alvo: {self.character.labels['target'].text()}")
            self.map.update_state(state)
            self._refresh_controls()
        elif e == "engine_start":
            caps = frozenset(ActionKind(c) for c in d["capabilities"])
            self.capabilities.set_adapter(d["adapter"], caps)
        elif e == "intent" and d["intent"]["kind"] == "move_to":
            p = d["intent"]["params"]
            self.map.set_pending_target(Position(p["x"], p["y"], p["z"]))
        elif e == "paused":
            self.pause_reason = d
            manual = d.get("resume_on") == "dialog_closed"
            self.banner.setText(("⚠ Missao: intervencao manual necessaria. " if manual else "⏸ ")
                                + d["reason"] + (". Aceite/entregue no jogo; a retomada ocorre quando a janela "
                                                 "for observada fechada ou ao confirmar." if manual else ""))
            self.banner.show()
        elif e == "resumed":
            self.pause_reason = None
            self.banner.hide()
        elif e == "script_error":
            self.editor.editor.mark_error(d.get("line"))
            self.editor.set_run_status("Erro no script", f"{d.get('error')}" + (f" (linha {d['line']})" if d.get("line") else ""))
        elif e in ("engine_stop", "session_error"):
            self.banner.hide()
            detail = d.get("reason") or d.get("error", "")
            self.statusBar().showMessage(f"Fim: {detail}", 10000)

    def on_status(self, status: SessionStatus) -> None:
        if status is SessionStatus.FAILED:
            reason = (self.session.report.reason if self.session and self.session.report else
                      (self.session.error if self.session else ""))
            if self.editor.editor.error_line is None:
                self.editor.set_run_status("Falhou", reason)
        else:
            self.editor.set_run_status(STATUS_TEXT[status], "Sem execucao automatica" if status in
                                       (SessionStatus.STOPPED, SessionStatus.IDLE) else self.source_kind.label)
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        st = self.status
        idle = st in (None, SessionStatus.IDLE, SessionStatus.STOPPED, SessionStatus.FAILED)
        self.actions_run["start"].setEnabled(idle)
        self.actions_run["pause"].setEnabled(st is SessionStatus.RUNNING)
        self.actions_run["resume"].setEnabled(st is SessionStatus.PAUSED)
        self.actions_run["stop"].setEnabled(st in (SessionStatus.RUNNING, SessionStatus.PAUSED))
        manual = st is SessionStatus.PAUSED and bool(self.pause_reason) and \
            self.pause_reason.get("resume_on") == "dialog_closed"
        self.manual_action.setEnabled(manual)
        self.source_combo.setEnabled(idle)
        tick = f" · ciclo {self.last_state.tick}" if self.last_state else ""
        self.status_label.setText(f"Fonte: {self.source_kind.label} · Motor: {STATUS_TEXT[st]}{tick}")

    # --- configuracoes e perfil ------------------------------------------------------------
    def open_config(self) -> None:
        dlg = ConfigDialog(self.profile, self.capabilities.adapter_name, self.capabilities.capabilities,
                           state_provider=lambda: self.last_state, parent=self)
        if dlg.exec():
            self.apply_profile(dlg.result_profile)

    def apply_profile(self, profile: Profile) -> None:
        self.profile = profile
        profile.save(self.profile_path)
        self._apply_profile_to_views()

    def _apply_profile_to_views(self) -> None:
        z = self.profile.values["zona"]
        center = Position(*z["center"]) if z["center"] else None
        self.map.set_zone(center, z["radius"])
        name = self.profile.character or "(sem personagem)"
        self.docks["character"].setWindowTitle(f"Sessao / Personagem — perfil {self.profile.name}: {name}")

    def set_zone_center(self, p: Position) -> None:
        profile = self.profile.copy()
        profile.set("zona", "center", [p.x, p.y, p.z])
        self.apply_profile(profile)

    # --- persistencia de layout ----------------------------------------------------------------
    def _restore(self) -> None:
        geo = self.settings.value("geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        state = self.settings.value("state")
        if isinstance(state, QByteArray):
            self.restoreState(state)
        kind = self.settings.value("source_kind", SourceKind.REPLAY.value)
        path = self.settings.value("source_path", "")
        try:
            self.set_source(SourceKind(kind), path if path and Path(path).is_file() else None)
        except ValueError:
            self.set_source(SourceKind.REPLAY, None)
        script = self.settings.value("script", "")
        if script and Path(script).is_file():
            self.editor.open_file(script)  # reabre o arquivo recente SEM executar
        self.tabs.setCurrentIndex(int(self.settings.value("tab", 0)))

    def save_layout(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        self.settings.setValue("source_kind", self.source_kind.value)
        self.settings.setValue("source_path", str(self.source_path) if self.source_path else "")
        self.settings.setValue("script", str(self.editor.path) if self.editor.path else "")
        self.settings.setValue("tab", self.tabs.currentIndex())
        self.settings.sync()

    def closeEvent(self, event) -> None:
        if self.editor.dirty and not self.confirm("Sair", "Ha alteracoes nao salvas no script. Sair mesmo assim?"):
            event.ignore()
            return
        if self.session and not self.session.finished:
            self.session.stop()
            self.session.join(2.0)
        for pid in list(self.plugins.plugins):
            self.plugins.disable(pid)
        self.save_layout()
        super().closeEvent(event)
