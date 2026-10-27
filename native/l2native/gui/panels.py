"""Paineis acoplaveis do workspace: sessao/personagem, entidades, capacidades,
eventos e plugins. Todo campo desconhecido e exibido como tal."""
from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QTableView, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..actions import ActionKind, capability_status
from ..adapters.replay import state_from_dict
from ..catalog import EntityCatalog
from ..geometry import distance_xy
from ..journal import Record
from ..model import Fact, GameState
from ..observation import WHY, ObservationMonitor, ReadinessStatus, Requirement
from ..plugins import PluginManager, PluginState
from ..profile import SECTIONS, Profile
from ..rules import RuleDecision, RuleEngine, state_time_ms
from .common import CATEGORIES, FILTERS, UNKNOWN_TEXT, describe, fact_text

UNKNOWN_STYLE = "color: #8d8d8d; font-style: italic;"


class SessionBridge(QObject):
    """Encaminha eventos da thread do motor para a thread Qt (conexao enfileirada)."""
    record = Signal(object)
    status = Signal(object)

    def listener(self, kind: str, payload: object) -> None:
        (self.record if kind == "record" else self.status).emit(payload)


class CharacterPanel(QWidget):
    FIELDS = (("level", "Nivel", False), ("hp", "HP", True), ("mp", "MP", True), ("exp", "EXP", False),
              ("self_position", "XYZ", False), ("target", "Alvo", False), ("in_combat", "Em combate", False),
              ("quest_dialog", "Janela de missao", False))

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        self.session_label = QLabel("Nenhuma sessao")
        self.session_label.setWordWrap(True)
        form.addRow("Sessao", self.session_label)
        self.labels: dict[str, QLabel] = {}
        for key, title, _ in self.FIELDS:
            lab = QLabel()
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.labels[key] = lab
            form.addRow(title, lab)
        self.updated = QLabel()
        form.addRow("Ultima atualizacao", self.updated)
        self.clear()

    def clear(self) -> None:
        for lab in self.labels.values():
            lab.setText(UNKNOWN_TEXT)
            lab.setStyleSheet(UNKNOWN_STYLE)
        self.updated.setText("nunca")

    def set_session(self, text: str) -> None:
        self.session_label.setText(text)

    def update_state(self, state: GameState) -> None:
        for key, _, percent in self.FIELDS:
            fact: Fact = getattr(state, key)
            self.labels[key].setText(fact_text(fact, percent=percent))
            self.labels[key].setStyleSheet("" if fact.known else UNKNOWN_STYLE)
        stamp = state.timestamp or time.strftime("%H:%M:%S") + " (local)"
        self.updated.setText(f"ciclo {state.tick} · {stamp}")


class EntityTable(QTableWidget):
    """Jogadores ou NPCs. Distingue 'ninguem a vista' de 'lista desconhecida'."""

    def __init__(self, field: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(0, 6, parent)
        self.field = field
        self.setHorizontalHeaderLabels(["Nome", "ID", "X", "Y", "Z", "Dist. XY"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.message = UNKNOWN_TEXT
        self._message_row("lista desconhecida")

    def _message_row(self, text: str) -> None:
        self.message = text
        self.setRowCount(1)
        item = QTableWidgetItem(text)
        item.setForeground(QColor("#8d8d8d"))
        self.setItem(0, 0, item)
        for c in range(1, 6):
            self.setItem(0, c, QTableWidgetItem(""))

    def update_state(self, state: GameState) -> None:
        fact = getattr(state, self.field)
        if not fact.known:
            self._message_row("lista desconhecida (a fonte nao fornece)")
            return
        if not fact.value:
            self._message_row("ninguem a vista (observado)")
            return
        self.message = ""
        me = state.self_position.value if state.self_position.known else None
        self.setRowCount(len(fact.value))
        for row, ent in enumerate(fact.value):
            pos = ent.position.value if ent.position.known else None
            cells = [ent.name or "?", "" if ent.npc_id is None else str(ent.npc_id)]
            cells += [f"{v:.0f}" for v in (pos.x, pos.y, pos.z)] if pos else [UNKNOWN_TEXT] * 3
            cells.append(f"{distance_xy(me, pos):.0f}" if (me and pos) else UNKNOWN_TEXT)
            for col, text in enumerate(cells):
                self.setItem(row, col, QTableWidgetItem(text))


class InfoPanel(QLabel):
    """Painel de texto simples (Alvo, Drops)."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setMargin(6)


class CapabilityPanel(QTableWidget):
    """Cada acao do contrato frente ao adaptador: Simulacao/Disponivel/Nao suportada/Desconhecida."""

    COLORS = {"Simulacao": "#1565c0", "Disponivel": "#2e7d32", "Nao suportada": "#8d8d8d", "Desconhecida": "#8a6d00"}

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(len(ActionKind), 2, parent)
        self.setHorizontalHeaderLabels(["Acao", "Estado"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.adapter_name: Optional[str] = None
        self.capabilities: frozenset[ActionKind] = frozenset()
        self.set_adapter(None, frozenset())

    def set_adapter(self, name: Optional[str], capabilities: frozenset[ActionKind]) -> None:
        self.adapter_name, self.capabilities = name, capabilities
        for row, kind in enumerate(ActionKind):
            status = capability_status(kind, name, capabilities).value
            self.setItem(row, 0, QTableWidgetItem(kind.value))
            item = QTableWidgetItem(status)
            item.setForeground(QColor(self.COLORS[status]))
            self.setItem(row, 1, item)

    def status_of(self, kind: ActionKind) -> str:
        return self.item(list(ActionKind).index(kind), 1).text()


class EventModel(QAbstractTableModel):
    HEADERS = ("Hora", "Ciclo", "Evento", "Detalhe")
    MAX_ROWS = 5000

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, int, str, str, str]] = []  # hora, ciclo, evento, detalhe, categoria

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return str(row[index.column()])
        if role == Qt.ItemDataRole.ForegroundRole and row[4] == "erros":
            return QColor("#c62828")
        if role == Qt.ItemDataRole.UserRole:
            return row[4]
        return None

    def add(self, record: Record) -> None:
        if len(self.rows) >= self.MAX_ROWS:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            self.rows.pop(0)
            self.endRemoveRows()
        n = len(self.rows)
        self.beginInsertRows(QModelIndex(), n, n)
        self.rows.append((time.strftime("%H:%M:%S"), record.tick, record.event, describe(record),
                          CATEGORIES.get(record.event, "motor")))
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.rows = []
        self.endResetModel()


class EventFilter(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.category: Optional[str] = None
        self.text = ""

    def set_filter(self, category: Optional[str], text: str) -> None:
        self.category, self.text = category, text.casefold()
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        row = self.sourceModel().rows[source_row]
        if self.category is None and row[4] == "observacoes":
            return False
        if self.category is not None and row[4] != self.category:
            return False
        return not self.text or self.text in f"{row[2]} {row[3]}".casefold()


class EventLog(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.model = EventModel()
        self.proxy = EventFilter()
        self.proxy.setSourceModel(self.model)
        self.view = QTableView(self)
        self.view.setModel(self.proxy)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.category = QComboBox(self)
        for label, cat in FILTERS:
            self.category.addItem(label, cat)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filtrar texto…")
        clear = QPushButton("Limpar", self)
        clear.clicked.connect(self.model.clear)
        self.category.currentIndexChanged.connect(self._apply)
        self.search.textChanged.connect(self._apply)
        top = QHBoxLayout()
        top.addWidget(QLabel("Mostrar:"))
        top.addWidget(self.category)
        top.addWidget(self.search, 1)
        top.addWidget(clear)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(top)
        layout.addWidget(self.view)

    def _apply(self) -> None:
        self.proxy.set_filter(self.category.currentData(), self.search.text())

    def add(self, record: Record) -> None:
        at_bottom = self.view.verticalScrollBar().value() >= self.view.verticalScrollBar().maximum() - 2
        self.model.add(record)
        if at_bottom:
            self.view.scrollToBottom()

    def visible_events(self) -> list[str]:
        return [self.proxy.index(r, 2).data() for r in range(self.proxy.rowCount())]


Confirm = Callable[[str, str], bool]


class PluginPanel(QWidget):
    """Lista de plugins com nome, versao, estado, permissoes e erros."""

    def __init__(self, manager: PluginManager, confirm: Confirm, choose_folder: Callable[[], Optional[str]],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.confirm = confirm
        self.choose_folder = choose_folder
        self.list = QListWidget(self)
        self.details = QLabel(self)
        self.details.setWordWrap(True)
        self.buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        for key, text in (("add", "Adicionar…"), ("enable", "Ativar"), ("disable", "Desativar"), ("remove", "Remover")):
            b = QPushButton(text, self)
            self.buttons[key] = b
            row.addWidget(b)
        self.buttons["add"].clicked.connect(self.add_dialog)
        self.buttons["enable"].clicked.connect(lambda: self.enable(self.selected()))
        self.buttons["disable"].clicked.connect(lambda: self.disable(self.selected()))
        self.buttons["remove"].clicked.connect(lambda: self.remove(self.selected()))
        self.list.currentRowChanged.connect(lambda _: self._show_details())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        layout.addWidget(self.details)
        self.refresh()

    def selected(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self) -> None:
        current = self.selected()
        self.manager.discover()
        self.list.clear()
        for info in self.manager.plugins.values():
            m = info.manifest
            label = f"{m.name} {m.version} — {info.state.value}" if m else f"{info.folder.name} — {info.state.value}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, info.id)
            if info.state in (PluginState.ERROR, PluginState.INCOMPATIBLE):
                item.setForeground(QColor("#c62828"))
            self.list.addItem(item)
            if info.id == current:
                self.list.setCurrentItem(item)
        if self.list.count() == 0:
            self.details.setText(f"Nenhum plugin em {self.manager.root}. Use Adicionar… para instalar uma pasta com plugin.json.")
        self._show_details()

    def _show_details(self) -> None:
        pid = self.selected()
        info = self.manager.plugins.get(pid) if pid else None
        for key in ("enable", "disable", "remove"):
            self.buttons[key].setEnabled(info is not None)
        if info is None:
            return
        self.buttons["enable"].setEnabled(info.state in (PluginState.DISABLED, PluginState.ERROR) and info.manifest is not None)
        self.buttons["disable"].setEnabled(info.state is PluginState.ENABLED)
        m = info.manifest
        perms = ", ".join(m.permissions) if m and m.permissions else "nenhuma"
        text = (f"<b>{m.name}</b> ({m.id}) v{m.version}, API {m.api_version}<br>{m.description}<br>"
                f"Origem: {info.folder}<br>Permissoes: {perms}") if m else f"Origem: {info.folder}"
        if info.error:
            text += f"<br><span style='color:#c62828'>Erro: {info.error}</span>"
        self.details.setText(text)

    def add_dialog(self) -> None:
        folder = self.choose_folder()
        if folder:
            self.install(folder)

    def install(self, folder: str) -> bool:
        if not self.confirm("Instalar plugin", f"Copiar o plugin de\n{folder}\npara {self.manager.root}?\n\n"
                            "Plugins sao codigo Python local executado sem sandbox."):
            return False
        try:
            self.manager.install(folder, confirmed=True)
        except (OSError, ValueError) as exc:
            self.details.setText(f"<span style='color:#c62828'>Falha ao instalar: {exc}</span>")
            return False
        self.refresh()
        return True

    def enable(self, pid: Optional[str]) -> bool:
        if not pid:
            return False
        info = self.manager.plugins[pid]
        perms = ", ".join(info.manifest.permissions) if info.manifest else ""
        if not self.confirm("Ativar plugin", f"Ativar '{pid}'? Isso executa o codigo do plugin neste processo.\n"
                            f"Permissoes declaradas: {perms or 'nenhuma'}"):
            return False
        try:
            self.manager.enable(pid, confirmed=True)
        except ValueError as exc:
            self.details.setText(f"<span style='color:#c62828'>{exc}</span>")
        self.refresh()
        return self.manager.plugins[pid].state is PluginState.ENABLED

    def disable(self, pid: Optional[str]) -> None:
        if pid:
            self.manager.disable(pid)
            self.refresh()

    def remove(self, pid: Optional[str]) -> bool:
        if not pid or not self.confirm("Remover plugin", f"Apagar a pasta do plugin '{pid}'?"):
            return False
        try:
            self.manager.remove(pid, confirmed=True)
        except (OSError, PermissionError) as exc:
            self.details.setText(f"<span style='color:#c62828'>{exc}</span>")
            return False
        self.refresh()
        return True


class _Table(QTableWidget):
    def __init__(self, columns: tuple[str, ...], parent: Optional[QWidget] = None) -> None:
        super().__init__(0, len(columns), parent)
        self.setHorizontalHeaderLabels(columns)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.verticalHeader().hide()

    def fill(self, rows: list[tuple[str, ...]]) -> None:
        self.setRowCount(len(rows))
        for r, cells in enumerate(rows):
            for c, text in enumerate(cells):
                self.setItem(r, c, QTableWidgetItem(text))

    def column(self, c: int) -> list[str]:
        return [self.item(r, c).text() for r in range(self.rowCount())]


class RuleDecisionPanel(_Table):
    """Decisao do RuleEngine a cada observacao, SEM execucao (registro 022).
    A regra escolhida por secao aparece como 'seria usada (sem execucao)'."""
    CHOSEN = "seria usada (sem execucao)"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(("Secao", "Regra", "Prior.", "ID", "Decisao", "Espera (ms)"), parent)
        self.engine: Optional[RuleEngine] = None
        self.profile: Optional[Profile] = None
        self.decisions: list[RuleDecision] = []

    def set_profile(self, profile: Profile) -> None:
        self.profile = profile
        self.engine = RuleEngine(profile.resolve_game_id)
        self.decisions = []
        self.fill([])

    def update_state(self, state: GameState) -> None:
        if self.profile is None or self.engine is None:
            return
        now = state_time_ms(state)
        rows, self.decisions = [], []
        for sec in SECTIONS:
            if not sec.rules or not self.profile.rules[sec.key]:
                continue
            decisions = self.engine.decide(sec.key, self.profile.rules[sec.key], state, now)
            chosen = RuleEngine.chosen(decisions)
            if chosen is not None:
                # simula o uso para que o intervalo apareca no proximo ciclo; nenhuma intencao e gerada
                self.engine.mark_used(sec.key, chosen.rule, now)
            for d in decisions:
                self.decisions.append(d)
                rows.append((sec.title, d.rule.name, str(d.rule.priority),
                             "" if d.game_id is None else str(d.game_id),
                             self.CHOSEN if d is chosen else d.status.value,
                             str(d.wait_ms) if d.wait_ms else ""))
        self.fill(rows)


class EntityCatalogPanel(_Table):
    """Entidades ja vistas na sessao (somente memoria)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(("Tipo", "Nome", "NPC ID", "Primeiro ciclo", "Ultimo ciclo", "Vezes", "Ultima XYZ"), parent)
        self.catalog = EntityCatalog()

    def clear_catalog(self) -> None:
        self.catalog.clear()
        self.fill([])

    def update_state(self, state: GameState) -> None:
        self.catalog.update(state)
        rows = []
        for e in sorted(self.catalog.entries.values(), key=lambda e: (e.kind, (e.name or "").casefold())):
            pos = "" if e.last_position is None else \
                f"{e.last_position.x:.0f}, {e.last_position.y:.0f}, {e.last_position.z:.0f}"
            rows.append(("NPC" if e.kind == "npc" else "Jogador", e.name or "?",
                         "" if e.npc_id is None else str(e.npc_id), str(e.first_tick), str(e.last_tick),
                         str(e.sightings), pos))
        self.fill(rows)


class ObservationPanel(QWidget):
    """Prontidao da fonte contra o conjunto minimo de observacoes."""
    COLORS = {ReadinessStatus.MET: "#2e7d32", ReadinessStatus.NOT_MET: "#c62828",
              ReadinessStatus.INSUFFICIENT: "#8d8d8d"}

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.monitor = ObservationMonitor()
        self.declared_label = QLabel("Adaptador: nenhum")
        self.declared_label.setWordWrap(True)
        self.table = _Table(("Requisito", "Estado", "Declarado", "Detalhe"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.declared_label)
        layout.addWidget(self.table)
        self.refresh()

    def reset(self, adapter: Optional[str] = None, declared: Optional[list[str]] = None) -> None:
        self.monitor = ObservationMonitor()
        self.monitor.set_declared(None if declared is None else frozenset(declared))
        if adapter is None:
            self.declared_label.setText("Adaptador: nenhum")
        elif declared is None:
            self.declared_label.setText(f"Adaptador {adapter}: nao declara campos (medido pelos dados)")
        else:
            self.declared_label.setText(f"Adaptador {adapter} declara: {', '.join(declared) or 'nenhum campo'}")
        self.refresh()

    def on_record(self, record: Record) -> None:
        if record.event == "observation":
            self.monitor.feed(state_from_dict(record.data["state"]), record.data.get("latency_ms"))
        elif record.event == "adapter_error" and not str(record.data.get("error", "")).startswith("close()"):
            self.monitor.feed_error()
        else:
            return
        self.refresh()

    def status_of(self, req: Requirement) -> str:
        return next(r.status.value for r in self.monitor.report() if r.requirement is req)

    def refresh(self) -> None:
        report = self.monitor.report()
        declared = {None: "-", True: "sim", False: "nao"}
        self.table.fill([(r.requirement.value, r.status.value, declared[r.declared], r.detail) for r in report])
        for row, r in enumerate(report):
            self.table.item(row, 1).setForeground(QColor(self.COLORS[r.status]))
            self.table.item(row, 0).setToolTip(WHY[r.requirement])
