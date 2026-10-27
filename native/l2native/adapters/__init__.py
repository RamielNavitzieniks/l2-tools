from .base import AdapterError, AdapterUnavailable, GameAdapter, SourceExhausted
from .live import LiveAdapter
from .mock import MockAdapter
from .replay import ReplayAdapter

# VisualAdapter fica em .visual para nao exigir o pacote de visao ao importar adaptadores.
__all__ = [
    "AdapterError", "AdapterUnavailable", "GameAdapter", "SourceExhausted",
    "LiveAdapter", "MockAdapter", "ReplayAdapter",
]
