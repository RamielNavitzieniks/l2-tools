"""Painel 'Sessoes locais descobertas' (LIVE-IDENT-001): janelas candidatas,
separadas dos personagens identificados. Somente leitura; vinculo manual.

Experimento 028: 'Identificar personagem' le o nome na regiao configurada da
janela SELECIONADA (worker fora da thread do Qt, cancelavel). O resultado
preenche so a coluna de identidade observada; nao muda a associacao manual,
nao conecta nada e nao inicia script.

Janela encontrada != personagem identificada != estado do jogo observado != acoes.
"""
from __future__ import annotations

import dataclasses
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QPushButton, QRubberBand, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..discovery import (CandidateRule, DiscoveredSession, DiscoveryStatus, LocalIdentity,
                         ScanResult, SessionDiscovery, WindowInfo)
from ..identity import IdentityReading, IdentityStatus, ReadingStatus, VisualIdentityProbe
from ..ocr import OcrBackend, OcrUnavailable
from ..vision.capture import CaptureUnavailable
from ..vision.frames import Frame, FrameSource, RegionOfInterest

AskBinding = Callable[[list[LocalIdentity], str], Optional[LocalIdentity]]
AskRule = Callable[[WindowInfo], Optional[CandidateRule]]
AskRegion = Callable[[Frame], Optional[RegionOfInterest]]
Confirm = Callable[[str, str], bool]

NOTICE = ("Janela localizada nao prova personagem logado. Identidade observada (OCR) nao e conexao com o jogo: "
          "NPCs, HP/MP, XYZ e acoes Live continuam indisponiveis; nada e enviado ao jogo e nenhum script e "
          "iniciado por uma identificacao.")

STATUS_MESSAGES = {
    IdentityStatus.MATCH: "Nome observado compativel com o perfil.",
    IdentityStatus.MISMATCH: "Nome observado DIFERENTE do perfil associado. Acoes dependentes desta identidade "
                             "ficam bloqueadas.",
    IdentityStatus.UNBOUND: "Nome observado; a janela nao tem associacao manual para comparar.",
    IdentityStatus.UNKNOWN: "OCR inconclusivo.",
    IdentityStatus.UNAVAILABLE: "Identificacao indisponivel.",
}


def _default_frames(w: WindowInfo) -> FrameSource:
    from ..vision.capture import WindowClientCapture
    return WindowClientCapture(w.hwnd, expected_pid=w.pid)


def _default_ocr() -> OcrBackend:
    from ..ocr import TesseractOcr
    return TesseractOcr()


class _Bridge(QObject):
    finished = Signal(object)


class RegionDialog(QDialog):
    """Mostra UM frame da janela selecionada (so em memoria) e deixa marcar o
    retangulo onde aparece o nome da propria personagem."""

    def __init__(self, frame: Frame, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Regiao do nome da personagem")
        self.frame = frame
        img = frame.image
        h, w = img.shape[:2]
        self._buf = img.copy()
        qimg = QImage(self._buf.data, w, h, 3 * w, QImage.Format.Format_BGR888)
        self.scale = min(1.0, 960 / w, 640 / h)
        self.view = QLabel()
        self.view.setPixmap(QPixmap.fromImage(qimg).scaled(QSize(int(w * self.scale), int(h * self.scale)),
                                                           Qt.AspectRatioMode.KeepAspectRatio))
        self.view.setFixedSize(int(w * self.scale), int(h * self.scale))
        self.band = QRubberBand(QRubberBand.Shape.Rectangle, self.view)
        self.selection = QRect()
        self._origin = QPoint()
        self.view.mousePressEvent = self._press
        self.view.mouseMoveEvent = self._move
        self.view.mouseReleaseEvent = self._release
        hint = QLabel("Arraste sobre o nome da SUA personagem (nao use chat, alvo ou lista de jogadores). "
                      "A imagem nao e salva.")
        hint.setWordWrap(True)
        self.info = QLabel()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.view)
        layout.addWidget(self.info)
        layout.addWidget(buttons)

    def _press(self, e) -> None:
        self._origin = e.position().toPoint()
        self.band.setGeometry(QRect(self._origin, QSize()))
        self.band.show()

    def _move(self, e) -> None:
        self.band.setGeometry(QRect(self._origin, e.position().toPoint()).normalized())

    def _release(self, e) -> None:
        self.set_selection(QRect(self._origin, e.position().toPoint()).normalized())

    def set_selection(self, rect: QRect) -> None:
        """Retangulo em pixels da imagem exibida."""
        self.selection = rect.intersected(QRect(QPoint(0, 0), self.view.size()))
        self.band.setGeometry(self.selection)
        self.band.show()
        roi = self.region()
        self.info.setText("" if roi else "Selecao vazia ou pequena demais.")

    def region(self) -> Optional[RegionOfInterest]:
        r = self.selection
        if r.isEmpty():
            return None
        vw, vh = self.view.width(), self.view.height()
        try:
            roi = RegionOfInterest(r.x() / vw, r.y() / vh, r.width() / vw, r.height() / vh, self.frame.client_size)
        except ValueError:
            return None
        return None if roi.layout_problem(self.frame.client_size) else roi


class SessionsPanel(QWidget):
    COLUMNS = ("Processo", "PID", "HWND", "Titulo", "Janela", "Perfil associado", "Identidade observada",
               "Confianca", "Lida ha", "Telemetria real", "Visto ha")
    COL_IDENTITY, COL_CONFIDENCE, COL_READ_AGO = 6, 7, 8

    def __init__(self, discovery: SessionDiscovery, config_path: Path, profile_name: Callable[[], str],
                 notify: Callable[[str, dict], None] = lambda e, d: None,
                 ask_binding: Optional[AskBinding] = None, ask_rule: Optional[AskRule] = None,
                 parent: Optional[QWidget] = None, *, ocr_factory: Optional[Callable[[], OcrBackend]] = None,
                 frame_source_factory: Optional[Callable[[WindowInfo], FrameSource]] = None,
                 ask_region: Optional[AskRegion] = None, confirm: Optional[Confirm] = None,
                 probe_options: Optional[dict[str, Any]] = None) -> None:
        super().__init__(parent)
        self.discovery = discovery
        self.config_path = Path(config_path)
        self.profile_name = profile_name
        self.notify = notify
        self.ask_binding = ask_binding or self._ask_binding
        self.ask_rule = ask_rule or self._ask_rule
        self.ask_region = ask_region or self._ask_region
        self.confirm = confirm or (lambda title, text: False)
        self.ocr_factory = ocr_factory or _default_ocr
        self.frame_source_factory = frame_source_factory or _default_frames
        self.probe_options = dict(probe_options or {})
        self.rows: list[DiscoveredSession] = []
        self.last: Optional[ScanResult] = None
        self.job: Optional[tuple[str, threading.Thread, threading.Event]] = None
        self._bridge = _Bridge(self)
        self._bridge.finished.connect(self._identified)

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
        self.buttons: dict[str, QPushButton] = {}

        def row(*specs):
            box = QHBoxLayout()
            for key, text, fn in specs:
                b = QPushButton(text)
                b.clicked.connect(fn)
                box.addWidget(b)
                self.buttons[key] = b
            box.addStretch(1)
            return box
        buttons = row(("refresh", "Atualizar", self.refresh), ("bind", "Associar…", self.bind_dialog),
                      ("unbind", "Desassociar", self.unbind_selected),
                      ("rule", "Criar regra desta janela…", self.rule_dialog),
                      ("suggested", "Regras sugeridas…", self.suggested_rules_dialog),
                      ("remove_rule", "Remover regra", self.remove_rule))
        identity = row(("identify", "Identificar personagem", self.identify_selected),
                       ("cancel", "Cancelar identificacao", self.cancel_identification),
                       ("region", "Configurar regiao do nome…", self.region_dialog),
                       ("clear", "Limpar observacao", self.clear_selected))
        self.buttons["cancel"].setEnabled(False)
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
        layout.addLayout(identity)
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
            if s.binding or s.reading:
                self.notify("session_closed", {"personagem": s.binding.label if s.binding else "-",
                                               "processo": s.window.process,
                                               "detalhe": "janela fechada: associacao e identidade observada "
                                                          "removidas"})
        self.message.setText(result.message)
        self.message.setStyleSheet("" if result.status is DiscoveryStatus.OK else "color: #c62828;")
        self.rows = result.sessions
        self._fill_table()
        return result

    def _fill_table(self) -> None:
        now = self.discovery.clock()
        self.table.setRowCount(len(self.rows))
        for r, s in enumerate(self.rows):
            w, st = s.window, s.states(now, self.discovery.matcher)
            obs = s.identity(now, self.discovery.matcher)
            busy = self.job is not None and self.job[0] == s.session_id
            ident = "identificando…" if busy else st["identidade"]
            conf = "-" if obs.confidence is None else f"{obs.confidence:.2f}"
            read_ago = "-" if s.reading is None else f"{max(0.0, now - s.reading.observed_at):.0f} s"
            cells = (w.process or f"? ({w.process_note})", str(w.pid), str(w.hwnd), w.title, st["janela"],
                     st["perfil"], ident, conf, read_ago, st["telemetria"], f"{max(0.0, now - s.last_seen):.0f} s")
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == self.COL_IDENTITY and not busy:
                    if obs.status is IdentityStatus.MISMATCH:
                        item.setForeground(QBrush(QColor("#c62828")))
                    elif obs.status is IdentityStatus.MATCH:
                        item.setForeground(QBrush(QColor("#2e7d32")))
                self.table.setItem(r, c, item)

    def selected(self) -> Optional[DiscoveredSession]:
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def select_row(self, row: int) -> None:
        self.table.selectRow(row)

    def row_of(self, session_id: str) -> int:
        return next((i for i, s in enumerate(self.rows) if s.session_id == session_id), -1)

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

    def suggested_rules_dialog(self) -> None:
        """Oferece as regras sugeridas (E13) que casam com janelas abertas; cada uma
        so e adotada com confirmacao explicita."""
        self.refresh()
        offers = self.discovery.suggestions
        if not offers:
            self.message.setText("Nenhuma regra sugerida casa com as janelas abertas (ou ja foi adotada).")
            return
        for s in offers:
            r = s.rule
            text = (f"Regra sugerida: {r.name}\nExecutavel: {r.process}\nClasse: {r.class_name}\n"
                    f"Titulo contem: {r.title_contains}\n\nOrigem: {s.source}\nLimite: {s.caveat}\n\n"
                    "Adotar esta regra? Ela soma as suas regras manuais e pode ser removida depois.")
            if self.confirm("Regra sugerida", text):
                adopted = dataclasses.replace(r, origin=f"{r.origin}; confirmada pelo operador "
                                                        f"em {time.strftime('%Y-%m-%d')}")
                try:
                    self.add_rule(adopted)
                except ValueError as exc:
                    self.message.setText(f"Regra recusada: {exc}")

    def remove_rule(self) -> None:
        name = self.rules.currentText()
        before = len(self.discovery.config.rules)
        self.discovery.config.rules = [r for r in self.discovery.config.rules if r.name != name]
        if len(self.discovery.config.rules) != before:
            self.discovery.config.regions.pop(name, None)
            self._save()
            self._fill_rules()
            self.refresh()

    # --- identidade observada ------------------------------------------------------------
    def region_dialog(self) -> None:
        s = self.selected()
        if s is None or not s.candidate:
            self.message.setText("Selecione uma janela candidata para configurar a regiao do nome.")
            return
        try:
            source = self.frame_source_factory(s.window)
            try:
                frame = source.grab_frame(None)
            finally:
                source.close()
        except CaptureUnavailable as exc:
            self.message.setText(f"Nao foi possivel capturar a janela para configurar a regiao: {exc}")
            return
        roi = self.ask_region(frame)
        if roi is None:
            return
        rule = s.rules[0]
        self.discovery.config.regions[rule] = roi
        self._save()
        self.refresh()
        self.message.setText(f"Regiao do nome salva para a regra '{rule}' ({roi.reference_size[0]}x"
                             f"{roi.reference_size[1]}). Identifique de novo.")
        self.notify("identity_region_set", {"regra": rule, "tamanho": list(roi.reference_size or ())})

    def identify_selected(self) -> bool:
        """Inicia a leitura em segundo plano. Devolve False se nao iniciou."""
        s = self.selected()
        if s is None or not s.candidate:
            self.message.setText("Selecione uma janela candidata para identificar a personagem.")
            return False
        if self.job is not None:
            self.message.setText("Ja ha uma identificacao em andamento.")
            return False
        region = self.discovery.region_for(s)
        session_id, window = s.session_id, s.window
        cancel = threading.Event()
        options = {"sleep": cancel.wait, **self.probe_options}   # espera interrompida pelo cancelamento

        def work() -> None:
            source = None
            try:
                ocr = self.ocr_factory()
                source = self.frame_source_factory(window)
                reading = VisualIdentityProbe(ocr, **options).probe(session_id, source, region, cancel)
            except (OcrUnavailable, CaptureUnavailable) as exc:
                reading = IdentityReading(session_id, ReadingStatus.UNAVAILABLE, time.time(), str(exc), region=region)
            except Exception as exc:   # falha inesperada vira estado visivel, nunca identidade
                reading = IdentityReading(session_id, ReadingStatus.UNAVAILABLE, time.time(),
                                          f"ERRO: {type(exc).__name__}: {exc}", region=region)
            finally:
                if source is not None:
                    source.close()
            self._bridge.finished.emit(reading)

        thread = threading.Thread(target=work, name="l2tools-identidade", daemon=True)
        self.job = (session_id, thread, cancel)
        self.buttons["identify"].setEnabled(False)
        self.buttons["cancel"].setEnabled(True)
        self.message.setText("Identificando a personagem (somente leitura)…")
        self._fill_table()
        thread.start()
        return True

    def cancel_identification(self) -> None:
        if self.job is not None:
            self.job[2].set()
            self.message.setText("Cancelando a identificacao…")

    def _identified(self, reading: IdentityReading) -> None:
        self.job = None
        self.buttons["identify"].setEnabled(True)
        self.buttons["cancel"].setEnabled(False)
        session = self.discovery.apply_reading(reading)
        if session is None:
            self.refresh()
            self.message.setText("Resultado descartado: a janela foi fechada ou mudou desde o pedido.")
            self.notify("identity_discarded", {"motivo": "sessao nao existe mais"})
            return
        obs = session.identity(self.discovery.clock(), self.discovery.matcher)
        text = STATUS_MESSAGES[obs.status]
        if obs.status in (IdentityStatus.UNKNOWN, IdentityStatus.UNAVAILABLE):
            text = f"{text} Motivo: {obs.reason}"
        self.message.setText(text)
        # diagnostico: estado e motivo, sem o texto lido
        self.notify("identity_observed", {"processo": session.window.process, "estado": obs.status.value,
                                          "confianca": obs.confidence, "motivo": obs.reason})
        self._fill_table()

    def clear_selected(self) -> None:
        s = self.selected()
        if s is not None and s.reading is not None:
            self.discovery.clear_reading(s.window.key)
            self.notify("identity_cleared", {"processo": s.window.process})
            self.refresh()

    def shutdown(self, timeout: float = 2.0) -> None:
        """Para a atualizacao periodica e cancela a leitura em andamento."""
        self.timer.stop()
        if self.job is not None:
            self.job[2].set()
            self.job[1].join(timeout)

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

    def _ask_region(self, frame: Frame) -> Optional[RegionOfInterest]:
        dlg = RegionDialog(frame, self)
        return dlg.region() if dlg.exec() == QDialog.DialogCode.Accepted else None
