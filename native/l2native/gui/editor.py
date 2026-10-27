"""Editor de scripts Python integrado ao workspace (UX-002 A)."""
from __future__ import annotations

import keyword
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRect, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextFormat
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QTextEdit, QToolButton, QVBoxLayout, QWidget)

NEW_SCRIPT = '''"""Novo script do L2-Tools Native. Executado sem sandbox: codigo local confiavel."""


def on_tick(bot):
    if bot.pause_if_quest_dialog():
        return
    me = bot.perception.position()
    if me is None:
        bot.log("posicao desconhecida")
        return
'''


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    f.setFontItalic(italic)
    return f


class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        kw = r"\b(" + "|".join(keyword.kwlist) + r")\b"
        self._rules = [  # (padrao, formato, grupo capturado a colorir)
            (QRegularExpression(kw), _fmt("#7c4dff", bold=True), 0),
            (QRegularExpression(r"\b(True|False|None|self|bot)\b"), _fmt("#c2185b"), 0),
            (QRegularExpression(r"\b[0-9]+(\.[0-9]+)?\b"), _fmt("#00838f"), 0),
            (QRegularExpression(r"\bdef\s+(\w+)"), _fmt("#1565c0", bold=True), 1),
            (QRegularExpression(r"(\"[^\"\n]*\"|'[^'\n]*')"), _fmt("#2e7d32"), 0),
            (QRegularExpression(r"#[^\n]*"), _fmt("#8d8d8d", italic=True), 0),
        ]

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt, group in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class _LineNumbers(QWidget):
    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._numbers = _LineNumbers(self)
        self.error_line: Optional[int] = None
        self.blockCountChanged.connect(lambda _: self.setViewportMargins(self.line_number_width(), 0, 0, 0))
        self.updateRequest.connect(self._on_update)
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)
        PythonHighlighter(self.document())

    def line_number_width(self) -> int:
        return 12 + self.fontMetrics().horizontalAdvance("9") * max(3, len(str(self.blockCount())))

    def _on_update(self, rect: QRect, dy: int) -> None:
        if dy:
            self._numbers.scroll(0, dy)
        else:
            self._numbers.update(0, rect.y(), self._numbers.width(), rect.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._numbers.setGeometry(QRect(cr.left(), cr.top(), self.line_number_width(), cr.height()))

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._numbers)
        painter.fillRect(event.rect(), self.palette().alternateBase())
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                is_error = self.error_line == number + 1
                painter.setPen(QColor("#c62828") if is_error else self.palette().placeholderText().color())
                painter.drawText(0, top, self._numbers.width() - 6, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, str(number + 1))
            block = block.next()
            top, bottom = bottom, bottom + round(self.blockBoundingRect(block).height())
            number += 1

    def mark_error(self, line: Optional[int]) -> None:
        self.error_line = line
        selections = []
        if line:
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(198, 40, 40, 60))
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            block = self.document().findBlockByNumber(line - 1)
            if block.isValid():
                sel.cursor = self.textCursor()
                sel.cursor.setPosition(block.position())
                sel.cursor.clearSelection()
                selections.append(sel)
        self.setExtraSelections(selections)
        self._numbers.update()


class ScriptEditor(QWidget):
    """Arquivo .py aberto, estado de alteracao e status de execucao.
    Abrir um arquivo NUNCA o executa; executar exige acao explicita."""

    pathChanged = Signal(object)
    runRequested = Signal()
    newRequested = Signal()
    openRequested = Signal()
    saveRequested = Signal()
    saveAsRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.path: Optional[Path] = None
        self.editor = CodeEditor(self)
        self.editor.textChanged.connect(self._on_text_changed)
        self._loading = False
        self.dirty = False

        bar = QHBoxLayout()
        for text, tip, signal in (("Novo", "Novo script", self.newRequested),
                                  ("Abrir…", "Abrir arquivo .py (nao executa)", self.openRequested),
                                  ("Salvar", "Salvar (Ctrl+S)", self.saveRequested),
                                  ("Salvar como…", "Salvar como", self.saveAsRequested),
                                  ("▶ Executar", "Salvar e iniciar sobre a fonte selecionada (F5)", self.runRequested)):
            b = QToolButton(self)
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(signal.emit)
            bar.addWidget(b)
        bar.addStretch(1)
        self.file_label = QLabel(self)
        bar.addWidget(self.file_label)

        self.warning = QLabel("Scripts sao codigo Python local executado SEM sandbox. Execute somente arquivos confiaveis.")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #8a6d00;")
        self.status = QLabel(self)
        self.set_run_status("Parado", "Sem execucao automatica")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.warning)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.status)
        self._refresh_label()

    def _on_text_changed(self) -> None:
        if not self._loading:
            self.dirty = True
            self.editor.mark_error(None)
            self._refresh_label()

    def _refresh_label(self) -> None:
        name = self.path.name if self.path else "(sem arquivo)"
        self.file_label.setText(f"{name}{' *' if self.dirty else ''}")

    def set_text(self, text: str, path: Optional[Path]) -> None:
        self._loading = True
        self.editor.setPlainText(text)
        self._loading = False
        self.path = Path(path) if path else None
        self.dirty = False
        self.editor.mark_error(None)
        self._refresh_label()
        self.pathChanged.emit(self.path)

    def new(self) -> None:
        self.set_text(NEW_SCRIPT, None)
        self.dirty = True
        self._refresh_label()

    def open_file(self, path: Path | str) -> None:
        path = Path(path)
        self.set_text(path.read_text(encoding="utf-8"), path)

    def save(self, path: Optional[Path | str] = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("defina um caminho (Salvar como)")
        target.write_text(self.editor.toPlainText(), encoding="utf-8")
        changed = target != self.path
        self.path = target
        self.dirty = False
        self._refresh_label()
        if changed:
            self.pathChanged.emit(self.path)
        return target

    def text(self) -> str:
        return self.editor.toPlainText()

    def set_run_status(self, state: str, detail: str = "") -> None:
        self.status.setText(f"<b>{state}</b>" + (f" — {detail}" if detail else ""))
