"""Ponto de entrada da GUI: python -m l2native.gui  (ou l2native-gui / L2ToolsNative.pyw)."""
from __future__ import annotations

import sys
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("A interface grafica requer PySide6: python -m pip install PySide6-Essentials", file=sys.stderr)
        return 3
    from .mainwindow import MainWindow

    app = QApplication.instance() or QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName("L2-Tools Native")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
