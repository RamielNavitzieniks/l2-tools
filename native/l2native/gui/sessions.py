"""Painel 'Sessoes locais descobertas' (LIVE-IDENT-001): janelas candidatas,
separadas dos personagens identificados. Somente leitura; vinculo manual."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..discovery import (CandidateRule, DiscoveredSession, DiscoveryStatus, LocalIdentity,
                         ScanResult, SessionDiscovery, WindowInfo)

AskBinding = Callable[[list[LocalIdentity], str], Optional[LocalIdentity]]
AskRule = Callable[[WindowInfo], Optional[CandidateRule]]

NOTICE = ("Janela localizada nao prova personagem logado. Identidade observada, NPCs, HP/MP, XYZ e acoes "
          "Live continuam indisponiveis; nada e enviado ao jogo.")


class SessionsPanel(QWidget):
    COLUMNS = ("Processo", "PID", "HWND", "Titulo", "Janela", "Perfil associado", "Identidade observada",
               "Telemetria real", "Visto ha")

    def __init__(self, discovery: SessionDiscovery, config_path: Path, profile_name: Callable[[], str],
                 notify: Callable[[str, dict], None] = lambda e, d: None,
                 ask_binding: Optional[AskBinding] = None, ask_rule: Optional[AskRule] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.discovery = discovery
        self.config_path = Path(config_path)
        self.profile_name = profile_name
        self.notify = notify
        self.ask_binding = ask_binding or self._ask_binding
        self.ask_rule = ask_rule or self._ask_rule
        self.rows: list[DiscoveredSession] = []
        self.last: Optional[ScanResult] = None

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.show_all = QCheckBox("Mostrar todas as janelas visiveis")
        self.show_all.toggled.connect(lambda _: self.refresh())
        self.auto = QCheckBox("Atualizar a cada 2 s")
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.auto.toggled.connect(lambda on: self.timer.start() if on else self.timer.stop())
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.rules = QComboBox()
        buttons = QHBoxLayout()
        for text, fn in (("Atualizar", self.refresh), ("Associar…", self.bind_dialog),
                         ("Desassociar", self.unbind_selected), ("Criar regra desta janela…", self.rule_dialog),
                         ("Remover regra", self.remove_rule)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        top = QHBoxLayout()
        top.addWidget(self.show_all)
        top.addWidget(self.auto)
        top.addStretch(1)
        top.addWidget(QLabel("Regras:"))
        top.addWidget(self.rules)
        notice = QLabel(NOTICE)
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #8d6e00;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.message)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        layout.addWidget(notice)
        self._fill_rules()

    # --- dados ------------------------------------------------------------------
    def _save(self) -> None:
        self.discovery.config.save(self.config_path)

    def _fill_rules(self) -> None:
        self.rules.clear()
        for r in self.discovery.config.rules:
            self.rules.addItem(r.name)
        if not self.discovery.config.rules:
            self.rules.addItem("(nenhuma)")

    def refresh(self) -> ScanResult:
        result = self.discovery.scan(include_all=self.show_all.isChecked())
        self.last = result
        for s in result.closed:
            if s.binding:
                self.notify("session_closed", {"personagem": s.binding.label, "processo": s.window.process,
                                               "detalhe": "janela fechada: associacao removida"})
        self.message.setText(result.message)
        self.message.setStyleSheet("" if result.status is DiscoveryStatus.OK else "color: #c62828;")
        self.rows = result.sessions
        now = time.time()
        self.table.setRowCount(len(self.rows))
        for r, s in enumerate(self.rows):
            w, st = s.window, s.states()
            cells = (w.process or f"? ({w.process_note})", str(w.pid), str(w.hwnd), w.title, st["janela"],
                     st["perfil"], st["identidade"], st["telemetria"], f"{max(0.0, now - s.last_seen):.0f} s")
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
        return result

    def selected(self) -> Optional[DiscoveredSession]:
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def select_row(self, row: int) -> None:
        self.table.selectRow(row)

    # --- vinculo manual ------------------------------------------------------------
    def bind(self, session: DiscoveredSession, identity: LocalIdentity) -> None:
        if not session.candidate:
            raise ValueError("so janelas candidatas (que casam com uma regra) podem ser associadas")
        self.discovery.bind(session.window.key, identity)
        self._save()
        self.notify("session_bound", {"personagem": identity.label, "perfil": identity.profile,
                                      "origem": identity.origin})
        self.refresh()

    def bind_dialog(self) -> None:
        s = self.selected()
        if s is None:
            self.message.setText("Selecione uma janela candidata para associar.")
            return
        identity = self.ask_binding(self.discovery.config.identities, self.profile_name())
        if identity is None:
            return
        try:
            self.bind(s, identity)
        except (KeyError, ValueError) as exc:
            self.message.setText(f"Associacao recusada: {exc}")

    def unbind_selected(self) -> None:
        s = self.selected()
        if s is not None and s.binding:
            label = s.binding.label
            self.discovery.unbind(s.window.key)
            self.notify("session_unbound", {"personagem": label})
            self.refresh()

    # --- regras -------------------------------------------------------------------------
    def add_rule(self, rule: CandidateRule) -> None:
        if any(r.name.casefold() == rule.name.casefold() for r in self.discovery.config.rules):
            raise ValueError(f"ja existe regra '{rule.name}'")
        self.discovery.config.rules.append(rule)
        self._save()
        self._fill_rules()
        self.refresh()

    def rule_dialog(self) -> None:
        s = self.selected()
        if s is None:
            self.message.setText("Marque 'Mostrar todas as janelas', selecione a janela do cliente e tente de novo.")
            return
        rule = self.ask_rule(s.window)
        if rule is None:
            return
        try:
            self.add_rule(rule)
        except ValueError as exc:
            self.message.setText(f"Regra recusada: {exc}")

    def remove_rule(self) -> None:
        name = self.rules.currentText()
        before = len(self.discovery.config.rules)
        self.discovery.config.rules = [r for r in self.discovery.config.rules if r.name != name]
        if len(self.discovery.config.rules) != before:
            self._save()
            self._fill_rules()
            self.refresh()

    # --- dialogos padrao -------------------------------------------------------------------
    def _ask_binding(self, known: list[LocalIdentity], profile: str) -> Optional[LocalIdentity]:
        labels = [i.label for i in known]
        label, ok = QInputDialog.getItem(self, "Associar janela", "Personagem (informado por voce, nao pelo jogo):",
                                         labels, 0, True)
        if not ok or not label.strip():
            return None
        prev = next((i.profile for i in known if i.label == label), "")
        prof, ok = QInputDialog.getText(self, "Associar janela", "Perfil local:", text=prev or profile)
        return LocalIdentity(label.strip(), prof.strip()) if ok else None

    def _ask_rule(self, w: WindowInfo) -> Optional[CandidateRule]:
        if not w.process:
            self.message.setText(f"Executavel desconhecido ({w.process_note}); a regra usaria so a classe.")
        name, ok = QInputDialog.getText(self, "Nova regra", "Nome da regra:", text=w.process or w.class_name)
        if not ok or not name.strip():
            return None
        evidence, ok = QInputDialog.getText(self, "Nova regra", "Evidencia (como voce sabe que e o cliente?):")
        if not ok or not evidence.strip():
            self.message.setText("Regra nao criada: descreva a evidencia.")
            return None
        title, ok = QInputDialog.getText(self, "Nova regra", "Trecho do titulo (opcional; vazio = nao usar):")
        if not ok:
            return None
        try:
            return CandidateRule.from_window(w, name.strip(), evidence.strip(), use_title=title.strip())
        except ValueError as exc:
            self.message.setText(f"Regra recusada: {exc}")
            return None
