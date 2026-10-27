"""Janela principal (workspace): mapa/editor no centro, paineis acoplaveis em volta."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDockWidget, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QTabWidget, QToolBar, QVBoxLayout, QWidget)

from ..actions import ActionKind
from ..adapters.factory import SourceKind
from ..adapters.replay import state_from_dict
from ..discovery import DiscoveryConfig, SessionDiscovery, WindowSource
from ..engine import EngineConfig
from ..journal import Record
from ..model import GameState, Position
from ..plugins import PluginManager
from ..profile import Profile
from ..routes import RouteGraph
from ..session import Session, SessionStatus
from .config import ConfigDialog
from .editor import ScriptEditor
from .mapview import LAYERS, MapView
from .panels import (CapabilityPanel, CharacterPanel, EntityCatalogPanel, EntityTable, EventLog, InfoPanel,
                     ObservationPanel, PluginPanel, RuleDecisionPanel, SessionBridge)
from .sessions import SessionsPanel

NATIVE_DIR = Path(__file__).resolve().parents[2]
EXAMPLES = (
    ("Missao manual (replay)", "scripts/quest_watch.py", SourceKind.REPLAY, "fixtures/quest_dialog.jsonl"),
    ("Seguir Sarynne (replay)", "scripts/follow_sarynne.py", SourceKind.REPLAY, "fixtures/follow_sarynne.jsonl"),
    ("Seguir Sarynne (simulado)", "scripts/follow_sarynne.py", SourceKind.MOCK, "fixtures/follow_world.json"),
    ("Patrulha circular (simulado)", "scripts/circular_patrol.py", SourceKind.MOCK, "fixtures/patrol_world.json"),
    ("Caminhar por rota (simulado)", "scripts/route_walker.py", SourceKind.MOCK, "fixtures/route_world.json"),
    ("Cacador com politicas (simulado)", "scripts/hunter.py", SourceKind.MOCK, "fixtures/hunt_world.json"),
)
EXAMPLE_HUNT_PROFILE = "fixtures/profiles/cacador_simulado.json"
EXAMPLE_ROUTE = "fixtures/route_demo.json"   # rota padrao do route_walker, mostrada no mapa
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
    def __init__(self, config_path: Optional[Path] = None, confirm: Optional[Confirm] = None,
                 window_source: Optional[WindowSource] = None, sessions_kwargs: Optional[dict] = None) -> None:
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
        self.route: Optional[RouteGraph] = None
        self.route_path: Optional[Path] = None
        self.route_dirty = False
        self.route_last: Optional[int] = None

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
        self.decisions = RuleDecisionPanel()
        self.catalog = EntityCatalogPanel()
        self.observation = ObservationPanel()
        self.plugins = PluginManager(self.cfg_dir / "plugins", notify=self._plugin_event)
        self.plugin_panel = PluginPanel(self.plugins, self.confirm, self._choose_plugin_folder)
        discovery_path = self.cfg_dir / "discovery.json"
        try:
            discovery_cfg = DiscoveryConfig.load(discovery_path)
        except (OSError, ValueError, KeyError):
            discovery_cfg = DiscoveryConfig()
        self.discovery = (SessionDiscovery(window_source, discovery_cfg) if window_source is not None
                          else SessionDiscovery.for_platform(discovery_cfg))
        self.sessions = SessionsPanel(self.discovery, discovery_path, lambda: self.profile.name,
                                      notify=self._plugin_event, **(sessions_kwargs or {}))
        self.docks: dict[str, QDockWidget] = {}
        L, R, B = (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea,
                   Qt.DockWidgetArea.BottomDockWidgetArea)
        for key, title, widget, area in (
                ("character", "Sessao / Personagem", self.character, L), ("plugins", "Plugins", self.plugin_panel, L),
                ("target", "Alvo", self.target, R), ("players", "Jogadores", self.players, R),
                ("npcs", "NPCs", self.npcs, R), ("drops", "Drops", self.drops, R),
                ("capabilities", "Capacidades do adaptador", self.capabilities, R),
                ("observation", "Observacao minima", self.observation, R),
                ("sessions", "Sessoes locais descobertas", self.sessions, B),
                ("events", "Eventos", self.events, B), ("decisions", "Decisoes de regras (sem execucao)", self.decisions, B),
                ("catalog", "Catalogo de entidades", self.catalog, B)):
            dock = QDockWidget(title, self)
            dock.setObjectName(f"dock_{key}")
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.docks[key] = dock
        self.tabifyDockWidget(self.docks["players"], self.docks["npcs"])
        self.tabifyDockWidget(self.docks["npcs"], self.docks["drops"])
        self.tabifyDockWidget(self.docks["capabilities"], self.docks["observation"])
        self.tabifyDockWidget(self.docks["events"], self.docks["sessions"])
        self.tabifyDockWidget(self.docks["events"], self.docks["decisions"])
        self.tabifyDockWidget(self.docks["decisions"], self.docks["catalog"])
        self.docks["players"].raise_()
        self.docks["capabilities"].raise_()
        self.docks["events"].raise_()

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
        self.map.pointPicked.connect(self.on_point_picked)
        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)
        self._default_layout = self.saveState()
        self._restore()
        self._apply_profile_to_views()
        self._refresh_controls()
        self.sessions.refresh()   # somente leitura: lista janelas, nao executa nada
        if self.profile_migrated:
            self.statusBar().showMessage("Perfil migrado ao carregar: " + "; ".join(self.profile_migrated), 15000)

    # --- utilitarios ---------------------------------------------------------
    def _ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def _choose_plugin_folder(self) -> Optional[str]:
        return QFileDialog.getExistingDirectory(self, "Pasta do plugin (com plugin.json)", str(NATIVE_DIR / "plugins")) or None

    def _load_profile(self) -> Profile:
        self.profile_migrated: list[str] = []
        if self.profile_path.is_file():
            try:
                profile = Profile.load(self.profile_path)
            except (OSError, ValueError, KeyError):
                return Profile.default()
            if profile.migrated:
                # grava ja no formato atual; o original fica ao lado como backup
                self.profile_migrated = list(profile.migrated)
                backup = self.profile_path.with_suffix(".v1.json")
                if not backup.exists():
                    backup.write_bytes(self.profile_path.read_bytes())
                profile.save(self.profile_path)
                profile.migrated = []   # ja gravado; o aviso fica na barra de status
            return profile
        return Profile.default()

    def _plugin_event(self, event: str, data: dict) -> None:
        self.events.add(Record(0, 0, event, data))
        if event == "plugin_error":
            self.plugin_panel.refresh()

    # --- barra de ferramentas e menus ---------------------------------------------
    def _build_toolbar(self) -> None:
        tb = QToolBar("Execucao", self)
        tb.setObjectName("toolbar_run")
        self.addToolBar(tb)
        self.source_combo = QComboBox()
        for kind in SourceKind:
            self.source_combo.addItem(kind.label, kind.value)   # valor simples: o PySide6 6.12 devolve enums str como str
        self.source_combo.currentIndexChanged.connect(lambda _: self.set_source(SourceKind(self.source_combo.currentData()), None))
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
        r = self.menuBar().addMenu("&Rota")
        r.addAction("Nova rota", self.new_route)
        r.addAction("Abrir rota…", self.open_route_dialog)
        r.addAction("Salvar rota como…", self.save_route_dialog)
        r.addSeparator()
        self.route_edit_action = r.addAction("Editar rota (clique no mapa adiciona no)")
        self.route_edit_action.setCheckable(True)
        r.addAction("Desfazer ultimo no", self.undo_route_node)
        r.addAction("Menor caminho entre nos…", self.shortest_path_dialog)
        c = self.menuBar().addMenu("&Configuracoes")
        c.addAction("Perfil e politicas…", self.open_config)
        v = self.menuBar().addMenu("&Visualizar")
        for dock in self.docks.values():
            v.addAction(dock.toggleViewAction())
        layers = v.addMenu("Camadas do mapa")
        self.layer_actions: dict[str, QAction] = {}
        for key, title in LAYERS:
            act = layers.addAction(title)
            act.setCheckable(True)
            act.setChecked(self.map.layers[key])
            act.toggled.connect(lambda on, k=key: self.map.set_layer(k, on))
            self.layer_actions[key] = act
        v.addSeparator()
        v.addAction("Mapa", lambda: self.tabs.setCurrentWidget(self.map))
        v.addAction("Script", lambda: self.tabs.setCurrentWidget(self.editor))
        v.addAction("Restaurar layout padrao", lambda: self.restoreState(self._default_layout))

    # --- fonte e script -------------------------------------------------------------
    def set_source(self, kind: SourceKind, path: Optional[Path | str]) -> None:
        self.source_kind = kind
        self.source_path = Path(path) if path else None
        if self.source_combo.currentData() != kind.value:
            self.source_combo.blockSignals(True)
            self.source_combo.setCurrentIndex(list(SourceKind).index(kind))
            self.source_combo.blockSignals(False)
        self.source_button.setEnabled(kind is not SourceKind.LIVE)
        if kind is SourceKind.LIVE:
            self.source_label.setText(" LiveAdapter nao implementado (janelas locais: aba Sessoes locais descobertas) ")
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
        if script.endswith("route_walker.py") and self._may_discard_route():
            self.load_route(NATIVE_DIR / (self.profile.values["rota"]["route_file"] or EXAMPLE_ROUTE))
        if script.endswith("hunter.py") and self.confirm(
                "Perfil do exemplo", "Usar o perfil sintetico do cacador (regras, catalogo, filtros e zona)? "
                "O perfil atual e guardado como profile.anterior.json."):
            if self.profile_path.is_file():
                backup = self.profile_path.with_name("profile.anterior.json")
                backup.write_bytes(self.profile_path.read_bytes())
            self.apply_profile(Profile.load(NATIVE_DIR / EXAMPLE_HUNT_PROFILE))
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
        # o perfil inteiro vai junto: hunter.py executa as politicas dele (no Simulado)
        params = {**self.profile.script_params(), "profile": self.profile.to_dict()}
        self.session = Session(self.source_kind, self.source_path, self.editor.path,
                               params=params, config=config)
        self.session.subscribe(self.bridge.listener)
        self.map.reset()
        self.character.clear()
        self.catalog.clear_catalog()
        self.decisions.set_profile(self.profile)
        self.observation.reset()
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
            self.catalog.update_state(state)
            self.decisions.update_state(state)
            self.observation.on_record(record)
            self._refresh_controls()
        elif e == "adapter_error":
            self.observation.on_record(record)
        elif e == "engine_start":
            caps = frozenset(ActionKind(c) for c in d["capabilities"])
            self.capabilities.set_adapter(d["adapter"], caps)
            self.observation.reset(d["adapter"], d.get("observes"))
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
        self.decisions.set_profile(profile)

    def _apply_profile_to_views(self) -> None:
        z = self.profile.values["zona"]
        center = Position(*z["center"]) if z["center"] else None
        self.map.set_zone(center, z["radius"])
        name = self.profile.character or "(sem personagem)"
        self.docks["character"].setWindowTitle(f"Sessao / Personagem — perfil {self.profile.name}: {name}")

    # --- rota (editor no mapa) ------------------------------------------------------------------
    def _show_route(self, path_nodes: Optional[list[int]] = None) -> None:
        self.map.set_route(self.route, path_nodes, self.route_last)

    def _may_discard_route(self) -> bool:
        return not self.route_dirty or self.confirm("Rota alterada", "Descartar a rota nao salva?")

    def new_route(self) -> None:
        if not self._may_discard_route():
            return
        self.route, self.route_path, self.route_dirty, self.route_last = RouteGraph(), None, False, None
        self.route_edit_action.setChecked(True)
        self._show_route()
        self.tabs.setCurrentWidget(self.map)

    def load_route(self, path: Path | str) -> bool:
        try:
            self.route = RouteGraph.load(path)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Rota invalida", str(exc))
            return False
        self.route_path, self.route_dirty = Path(path), False
        self.route_last = max(self.route.nodes, default=None)
        self._show_route()
        self.statusBar().showMessage(f"Rota: {Path(path).name} · origem: {self.route.provenance}", 10000)
        return True

    def open_route_dialog(self) -> None:
        if not self._may_discard_route():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Abrir rota", str(NATIVE_DIR / "fixtures"), "Rota (*.json)")
        if path:
            self.load_route(path)

    def save_route(self, path: Path | str) -> None:
        if self.route is None:
            raise ValueError("nenhuma rota aberta")
        self.route.save(path)
        self.route_path, self.route_dirty = Path(path), False
        profile = self.profile.copy()
        profile.set("rota", "route_file", str(path))
        self.apply_profile(profile)

    def save_route_dialog(self) -> None:
        if self.route is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Salvar rota", str(self.cfg_dir / "rota.json"), "Rota (*.json)")
        if path:
            self.save_route(path)

    def on_point_picked(self, p: Position) -> None:
        self.statusBar().showMessage(f"Ponto: {p.x:.0f}, {p.y:.0f}, {p.z:.0f}", 5000)
        if self.route_edit_action.isChecked():
            self.add_route_node(p)

    def add_route_node(self, p: Position) -> int:
        if self.route is None:
            self.route = RouteGraph()
        connect = self.route_last if self.route_last in self.route.nodes else None
        self.route_last = self.route.add_node(p, connect)
        self.route_dirty = True
        self._show_route()
        return self.route_last

    def undo_route_node(self) -> None:
        if self.route is None or self.route_last not in self.route.nodes:
            return
        neighbors = self.route.neighbors(self.route_last)
        self.route.remove_node(self.route_last)
        self.route_last = max(neighbors) if neighbors else max(self.route.nodes, default=None)
        self.route_dirty = True
        self._show_route()

    def show_shortest_path(self, start: int, goal: int) -> Optional[list[int]]:
        if self.route is None:
            return None
        path = self.route.shortest_path(start, goal)
        self._show_route(path)
        msg = (f"Sem ligacao entre {start} e {goal}" if path is None else
               f"Caminho {' > '.join(map(str, path))} · {self.route.length(path):.0f} u")
        self.statusBar().showMessage(msg, 10000)
        return path

    def shortest_path_dialog(self) -> None:
        if self.route is None or len(self.route.nodes) < 2:
            QMessageBox.information(self, "Rota", "Abra ou desenhe uma rota com ao menos 2 nos.")
            return
        ids = sorted(self.route.nodes)
        start, ok = QInputDialog.getInt(self, "Menor caminho", "No de origem:", ids[0], ids[0], ids[-1])
        if not ok:
            return
        goal, ok = QInputDialog.getInt(self, "Menor caminho", "No de destino:", ids[-1], ids[0], ids[-1])
        if ok:
            try:
                self.show_shortest_path(start, goal)
            except KeyError as exc:
                QMessageBox.warning(self, "Menor caminho", str(exc))

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
        if not self._may_discard_route():
            event.ignore()
            return
        if self.session and not self.session.finished:
            self.session.stop()
            self.session.join(2.0)
        for pid in list(self.plugins.plugins):
            self.plugins.disable(pid)
        self.save_layout()
        super().closeEvent(event)
