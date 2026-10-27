"""Duplo clique no Windows abre a GUI sem console (pythonw)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from l2native.gui.app import main  # noqa: E402

sys.exit(main())
