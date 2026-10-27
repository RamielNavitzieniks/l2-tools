"""Janela de configuracoes do perfil (UX-001 itens 3, 4 e 7).

Arvore de secoes a esquerda, pagina tipada a direita. Cada pagina mostra o
estado do backend das acoes de que dependeria; configurar nao torna funcional.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..actions import ActionKind, CapabilityStatus, capability_status
from ..model import GameState
from ..profile import SECTIONS, Profile, Section, Setting, SkillEntry
from ..rules import Condition, ConditionField, FieldType, FilterRule, Operator, Rule, RuleKind, TargetKind, ordered


def section_status(section: Section, adapter: Optional[str], caps: frozenset[ActionKind]) -> str:
    if not section.needs:
        return "Somente configuracao do motor" if section.key == "geral" else \
            f"{CapabilityStatus.UNSUPPORTED.value}: o contrato de acoes nao cobre esta funcao"
    statuses = [capability_status(k, adapter, caps) for k in section.needs]
    order = [CapabilityStatus.UNKNOWN, CapabilityStatus.UNSUPPORTED, CapabilityStatus.SIMULATION, CapabilityStatus.AVAILABLE]
    worst = min(statuses, key=order.index)
    needs = ", ".join(k.value for k in section.needs)
    return f"{worst.value} (depende de: {needs})"


# --- campos tipados ------------------------------------------------------------
class SettingField:
    def __init__(self, setting: Setting, value: Any) -> None:
        self.setting = setting
        t = setting.type
        if t == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
            self.get = w.isChecked
        elif t == "int":
            w = QSpinBox()
            w.setRange(int(setting.min if setting.min is not None else -10**9),
                       int(setting.max if setting.max is not None else 10**9))
            w.setValue(int(value))
            self.get = w.value
        elif t == "float":
            w = QDoubleSpinBox()
            w.setRange(setting.min or -1e9, setting.max or 1e9)
            w.setValue(float(value))
            self.get = w.value
        elif t == "choice":
            w = QComboBox()
            w.addItems(list(setting.choices))
            w.setCurrentText(value)
            self.get = w.currentText
        elif t == "position":
            w = QLineEdit("" if value is None else ", ".join(f"{v:g}" for v in value))
            w.setPlaceholderText("x, y, z (vazio = nao definido)")
            self.get = lambda: self._parse_position(w.text())
        else:
            w = QLineEdit(str(value))
            self.get = w.text
        if setting.locked:
            w.setEnabled(False)
        if setting.help:
            w.setToolTip(setting.help)
        self.widget = w

    @staticmethod
    def _parse_position(text: str) -> Optional[list[float]]:
        if not text.strip():
            return None
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 3:
            raise ValueError("posicao deve ter 3 numeros: x, y, z")
        return [float(p) for p in parts]


# --- regras -----------------------------------------------------------------------
class RuleDialog(QDialog):
    def __init__(self, rule: Optional[Rule] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Regra")
        self.kind = QComboBox()
        self.kind.addItems([k.value for k in RuleKind])
        self.name = QLineEdit()
        self.target = QComboBox()
        self.target.addItems([t.value for t in TargetKind])
        self.priority = QSpinBox()
        self.priority.setRange(0, 100)
        self.priority.setValue(50)
        self.delay = QSpinBox()
        self.delay.setRange(0, 3_600_000)
        self.interval = QSpinBox()
        self.interval.setRange(0, 3_600_000)
        self.enabled = QCheckBox("Ativa")
        self.enabled.setChecked(True)
        self.conditions: list[Condition] = []
        self.cond_list = QListWidget()
        self.c_field = QComboBox()
        for f in ConditionField:
            self.c_field.addItem(f.label, f.value)   # valor simples: o PySide6 6.12 devolve enums str como str
        self.c_op = QComboBox()
        self.c_value = QLineEdit()
        self.c_field.currentIndexChanged.connect(self._sync_ops)
        add_c = QPushButton("Adicionar condicao")
        add_c.clicked.connect(self._add_condition)
        del_c = QPushButton("Remover condicao")
        del_c.clicked.connect(self._remove_condition)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828")

        form = QFormLayout()
        for label, w in (("Tipo", self.kind), ("Nome", self.name), ("Alvo", self.target),
                         ("Prioridade (0-100)", self.priority), ("Atraso (ms)", self.delay),
                         ("Intervalo (ms)", self.interval), ("", self.enabled)):
            form.addRow(label, w)
        crow = QHBoxLayout()
        for w in (self.c_field, self.c_op, self.c_value, add_c, del_c):
            crow.addWidget(w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("O ID no jogo vem do Catalogo de habilidades, pelo nome (perfil v2)."))
        layout.addWidget(QLabel("Condicoes (todas precisam ser verdadeiras; desconhecida bloqueia):"))
        layout.addWidget(self.cond_list)
        layout.addLayout(crow)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self._sync_ops()
        self.result_rule: Optional[Rule] = None
        if rule:
            self.kind.setCurrentText(rule.kind.value)
            self.name.setText(rule.name)
            self.target.setCurrentText(rule.target.value)
            self.priority.setValue(rule.priority)
            self.delay.setValue(rule.delay_ms)
            self.interval.setValue(rule.interval_ms)
            self.enabled.setChecked(rule.enabled)
            for c in rule.conditions:
                self._append(c)

    def _sync_ops(self) -> None:
        f = ConditionField(self.c_field.currentData())
        self.c_op.clear()
        ops = [Operator.EQ, Operator.NE] if f.type is FieldType.BOOL else list(Operator)
        self.c_op.addItems([o.value for o in ops])
        self.c_value.setPlaceholderText("sim / nao" if f.type is FieldType.BOOL else "numero")

    def _append(self, c: Condition) -> None:
        self.conditions.append(c)
        self.cond_list.addItem(c.describe())

    def _add_condition(self) -> None:
        f = ConditionField(self.c_field.currentData())
        raw = self.c_value.text().strip()
        try:
            if f.type is FieldType.BOOL:
                if raw.lower() not in ("sim", "nao", "não", "true", "false"):
                    raise ValueError("use sim ou nao")
                value: Any = raw.lower() in ("sim", "true")
            else:
                value = float(raw) if "." in raw else int(raw)
            self._append(Condition(f, Operator(self.c_op.currentText()), value))
            self.error.setText("")
        except ValueError as exc:
            self.error.setText(f"Condicao invalida: {exc}")

    def _remove_condition(self) -> None:
        row = self.cond_list.currentRow()
        if row >= 0:
            self.cond_list.takeItem(row)
            del self.conditions[row]

    def build(self) -> Rule:
        return Rule(RuleKind(self.kind.currentText()), self.name.text(), TargetKind(self.target.currentText()),
                    self.priority.value(), self.delay.value(), self.interval.value(), self.enabled.isChecked(),
                    tuple(self.conditions))

    def accept(self) -> None:
        try:
            self.result_rule = self.build()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()


class RuleTable(QWidget):
    COLUMNS = ("Ativa", "Tipo", "Nome", "Alvo", "Prior.", "Atraso", "Interv.", "ID (catalogo)", "Condicoes", "Agora")

    def __init__(self, rules: list[Rule], state_provider: Callable[[], Optional[GameState]],
                 id_text: Callable[[Rule], str] = lambda rule: "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.rules = list(rules)
        self.state_provider = state_provider
        self.id_text = id_text
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        row = QHBoxLayout()
        for text, fn in (("Adicionar…", self.add_dialog), ("Editar…", self.edit_dialog),
                         ("Remover", self.remove_selected), ("Ordenar por prioridade", self.sort)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        state = self.state_provider()
        self.table.setRowCount(len(self.rules))
        for r, rule in enumerate(self.rules):
            if state is None:
                now = "sem estado"
            else:
                now = {True: "verdadeira", False: "falsa", None: "desconhecida"}[rule.evaluate(state)]
            cells = ["sim" if rule.enabled else "nao", rule.kind.value, rule.name, rule.target.value,
                     str(rule.priority), str(rule.delay_ms), str(rule.interval_ms),
                     self.id_text(rule),
                     " E ".join(c.describe() for c in rule.conditions) or "(sempre)", now]
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)
        self.refresh()

    def add_dialog(self) -> None:
        dlg = RuleDialog(parent=self)
        if dlg.exec() and dlg.result_rule:
            self.add(dlg.result_rule)

    def edit_dialog(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        dlg = RuleDialog(self.rules[r], parent=self)
        if dlg.exec() and dlg.result_rule:
            self.rules[r] = dlg.result_rule
            self.refresh()

    def remove_selected(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            del self.rules[r]
            self.refresh()

    def sort(self) -> None:
        self.rules = ordered(self.rules)
        self.refresh()


class FilterTable(QWidget):
    def __init__(self, filters: list[FilterRule], actions: tuple[str, ...], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.filters = list(filters)
        self.actions = actions
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Acao", "Nome", "ID", "Prioridade"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.action = QComboBox()
        self.action.addItems(list(actions))
        self.name = QLineEdit()
        self.name.setPlaceholderText("nome")
        self.game_id = QLineEdit()
        self.game_id.setPlaceholderText("ID")
        self.priority = QSpinBox()
        self.priority.setRange(0, 100)
        self.priority.setValue(50)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828")
        add = QPushButton("Adicionar")
        add.clicked.connect(self._add_from_fields)
        rem = QPushButton("Remover")
        rem.clicked.connect(self._remove)
        row = QHBoxLayout()
        for w in (self.action, self.name, self.game_id, self.priority, add, rem):
            row.addWidget(w)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("A primeira linha que casar (maior prioridade primeiro) decide."))
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(self.error)
        self.refresh()

    def refresh(self) -> None:
        self.table.setRowCount(len(self.filters))
        for r, f in enumerate(self.filters):
            for c, text in enumerate((f.action, f.name, "" if f.game_id is None else str(f.game_id), str(f.priority))):
                self.table.setItem(r, c, QTableWidgetItem(text))

    def add(self, f: FilterRule) -> None:
        self.filters.append(f)
        self.filters.sort(key=lambda x: -x.priority)
        self.refresh()

    def _add_from_fields(self) -> None:
        try:
            gid = self.game_id.text().strip()
            self.add(FilterRule(self.action.currentText(), self.name.text(), int(gid) if gid else None,
                                self.priority.value()))
            self.error.setText("")
        except ValueError as exc:
            self.error.setText(str(exc))

    def _remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            del self.filters[r]
            self.refresh()


class SkillTable(QWidget):
    """Catalogo de habilidades/itens do perfil: unica fonte de IDs (registro 022).
    'Confirmado' so quando o ID foi visto no proprio cliente; nada e consultado."""
    COLUMNS = ("Nome", "ID", "Confirmado", "Nota")

    def __init__(self, skills: list[SkillEntry], on_change: Callable[[], None] = lambda: None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.skills = list(skills)
        self.on_change = on_change
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.name = QLineEdit()
        self.name.setPlaceholderText("nome (igual ao da regra)")
        self.game_id = QLineEdit()
        self.game_id.setPlaceholderText("ID")
        self.confirmed = QCheckBox("Confirmado no cliente")
        self.note = QLineEdit()
        self.note.setPlaceholderText("nota (onde foi confirmado)")
        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828")
        add = QPushButton("Adicionar/atualizar")
        add.clicked.connect(self._add_from_fields)
        rem = QPushButton("Remover")
        rem.clicked.connect(self._remove)
        row = QHBoxLayout()
        for w in (self.name, self.game_id, self.confirmed, self.note, add, rem):
            row.addWidget(w)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(self.error)
        self.refresh()

    def refresh(self) -> None:
        self.table.setRowCount(len(self.skills))
        for r, s in enumerate(self.skills):
            cells = (s.name, "" if s.game_id is None else str(s.game_id), "sim" if s.confirmed else "nao", s.note)
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.on_change()

    def put(self, entry: SkillEntry) -> None:
        """Adiciona ou substitui (mesmo nome, sem diferenciar maiusculas)."""
        self.skills = [s for s in self.skills if s.name.casefold() != entry.name.casefold()] + [entry]
        self.refresh()

    def lookup(self, name: str) -> Optional[SkillEntry]:
        return next((s for s in self.skills if s.name.casefold() == name.casefold()), None)

    def _add_from_fields(self) -> None:
        try:
            gid = self.game_id.text().strip()
            self.put(SkillEntry(self.name.text().strip(), int(gid) if gid else None,
                                self.confirmed.isChecked(), self.note.text().strip()))
            self.error.setText("")
        except ValueError as exc:
            self.error.setText(str(exc))

    def _remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            del self.skills[r]
            self.refresh()


SKILLS_PAGE = "_habilidades"


# --- dialogo -------------------------------------------------------------------------
class ConfigDialog(QDialog):
    def __init__(self, profile: Profile, adapter_name: Optional[str], capabilities: frozenset[ActionKind],
                 state_provider: Callable[[], Optional[GameState]] = lambda: None,
                 saved: Optional[Profile] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuracoes do perfil")
        self.resize(900, 600)
        self.original = saved or profile
        self.profile = profile.copy()
        self.fields: dict[str, dict[str, SettingField]] = {}
        self.rule_tables: dict[str, RuleTable] = {}
        self.filter_tables: dict[str, FilterTable] = {}
        self.skill_table = SkillTable(self.profile.skills, self._refresh_rule_ids)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.pages = QStackedWidget()
        items: dict[str, QTreeWidgetItem] = {}
        self.page_index: dict[str, int] = {}
        for sec in SECTIONS:
            item = QTreeWidgetItem([sec.title])
            item.setData(0, Qt.ItemDataRole.UserRole, sec.key)
            (items[sec.parent].addChild(item) if sec.parent else self.tree.addTopLevelItem(item))
            items[sec.key] = item
            self.page_index[sec.key] = self.pages.addWidget(self._page(sec, adapter_name, capabilities, state_provider))
        item = QTreeWidgetItem(["Catalogo de habilidades"])
        item.setData(0, Qt.ItemDataRole.UserRole, SKILLS_PAGE)
        self.tree.addTopLevelItem(item)
        items[SKILLS_PAGE] = item
        self.page_index[SKILLS_PAGE] = self.pages.addWidget(self._skills_page())
        self.tree.expandAll()
        self.tree.currentItemChanged.connect(
            lambda cur, _: cur and self.pages.setCurrentIndex(self.page_index[cur.data(0, Qt.ItemDataRole.UserRole)]))
        self.tree.setCurrentItem(items["geral"])

        self.name = QLineEdit(self.profile.name)
        self.character = QLineEdit(self.profile.character)
        self.character.setPlaceholderText("rotulo da personagem (sem login/senha)")
        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828")
        self.error.setWordWrap(True)
        self.info = QLabel(self._migration_text(profile))
        self.info.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(QLabel("Perfil:"))
        top.addWidget(self.name)
        top.addWidget(QLabel("Personagem:"))
        top.addWidget(self.character)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        for text, fn in (("Importar…", self.import_dialog), ("Exportar…", self.export_dialog),
                         ("Ver alteracoes", self.show_changes)):
            b = buttons.addButton(text, QDialogButtonBox.ButtonRole.ActionRole)
            b.clicked.connect(fn)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(self.pages)
        split.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(split, 1)
        layout.addWidget(self.info)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    @staticmethod
    def _migration_text(profile: Profile) -> str:
        if not profile.migrated:
            return ""
        return ("Perfil migrado ao carregar (" + "; ".join(profile.migrated) +
                "). Salve para gravar no formato atual.")

    def id_text(self, rule: Rule) -> str:
        entry = self.skill_table.lookup(rule.name)
        if entry is None:
            return "nao catalogada"
        if not entry.confirmed:
            return f"{entry.game_id or '-'} (nao confirmado)"
        return str(entry.game_id)

    def _refresh_rule_ids(self) -> None:
        for t in getattr(self, "rule_tables", {}).values():
            t.refresh()

    def _skills_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("<h3>Catalogo de habilidades</h3>"))
        note = QLabel("Fonte unica dos IDs usados pelas regras, casados pelo nome. Regra sem entrada "
                      "confirmada nunca fica pronta. Preencha apenas com IDs vistos no proprio cliente; "
                      "nada e baixado nem copiado de ferramentas de terceiros.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(self.skill_table, 1)
        return page

    def _page(self, sec: Section, adapter: Optional[str], caps: frozenset[ActionKind],
              state_provider: Callable[[], Optional[GameState]]) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel(f"<h3>{sec.title}</h3>")
        status = QLabel(f"Backend: <b>{section_status(sec, adapter, caps)}</b>")
        status.setObjectName(f"status_{sec.key}")
        layout.addWidget(title)
        layout.addWidget(status)
        if sec.note:
            note = QLabel(sec.note)
            note.setWordWrap(True)
            layout.addWidget(note)
        if sec.settings:
            form = QFormLayout()
            self.fields[sec.key] = {}
            for s in sec.settings:
                f = SettingField(s, self.profile.get(sec.key, s.key))
                self.fields[sec.key][s.key] = f
                form.addRow(s.label + (" 🔒" if s.locked else ""), f.widget)
            layout.addLayout(form)
        if sec.rules:
            t = RuleTable(self.profile.rules[sec.key], state_provider, self.id_text)
            self.rule_tables[sec.key] = t
            layout.addWidget(t, 1)
        if sec.filter_actions:
            t = FilterTable(self.profile.filters[sec.key], sec.filter_actions)
            self.filter_tables[sec.key] = t
            layout.addWidget(t, 1)
        layout.addStretch(0 if (sec.rules or sec.filter_actions) else 1)
        return page

    def collect(self) -> Profile:
        """Valida todos os campos e devolve o perfil editado (ValueError se invalido)."""
        p = self.profile.copy()
        p.name = self.name.text().strip() or "padrao"
        p.character = self.character.text().strip()
        for sec_key, fields in self.fields.items():
            for key, f in fields.items():
                p.set(sec_key, key, f.get())
        for sec_key, t in self.rule_tables.items():
            p.set_rules(sec_key, t.rules)
        for sec_key, t in self.filter_tables.items():
            p.set_filters(sec_key, t.filters)
        p.set_skills(self.skill_table.skills)
        return p

    def changes(self) -> list[str]:
        return self.original.diff(self.collect())

    def show_changes(self) -> None:
        try:
            text = "\n".join(self.changes()) or "Nenhuma alteracao."
        except ValueError as exc:
            text = f"Configuracao invalida: {exc}"
        QMessageBox.information(self, "Alteracoes nao salvas", text)

    def export_to(self, path: Path | str) -> None:
        self.collect().save(path)

    def import_from(self, path: Path | str) -> None:
        """Carrega um perfil exportado; campos sensiveis ou invalidos sao recusados (ValueError)."""
        self.replace_profile(Profile.load(path))

    def export_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Exportar perfil", "perfil.json", "Perfil (*.json)")
        if path:
            try:
                self.export_to(path)
            except ValueError as exc:
                self.error.setText(str(exc))

    def import_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar perfil", "", "Perfil (*.json)")
        if not path:
            return
        try:
            self.import_from(path)
        except (OSError, ValueError, KeyError) as exc:
            self.error.setText(f"Importacao recusada: {exc}")

    def replace_profile(self, imported: Profile) -> None:
        """Recarrega todos os campos com o perfil importado (alteracoes visiveis em 'Ver alteracoes')."""
        self.profile = imported.copy()
        self.info.setText(self._migration_text(imported))
        self.skill_table.skills = list(imported.skills)
        self.skill_table.refresh()
        self.name.setText(imported.name)
        self.character.setText(imported.character)
        for sec_key, fields in self.fields.items():
            for key, f in fields.items():
                w, v = f.widget, imported.get(sec_key, key)
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, QSpinBox):
                    w.setValue(int(v))
                elif isinstance(w, QDoubleSpinBox):
                    w.setValue(float(v))
                elif isinstance(w, QComboBox):
                    w.setCurrentText(v)
                elif f.setting.type == "position":
                    w.setText("" if v is None else ", ".join(f"{c:g}" for c in v))
                else:
                    w.setText(str(v))
        for sec_key, t in self.rule_tables.items():
            t.rules = list(imported.rules[sec_key])
            t.refresh()
        for sec_key, t in self.filter_tables.items():
            t.filters = list(imported.filters[sec_key])
            t.refresh()

    def accept(self) -> None:
        try:
            self.result_profile = self.collect()
        except ValueError as exc:
            self.error.setText(f"Corrija antes de salvar: {exc}")
            return
        super().accept()
