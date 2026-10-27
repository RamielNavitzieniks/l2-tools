"""Criacao de adaptadores a partir de um tipo de fonte e um caminho (CLI e GUI)."""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from .base import GameAdapter


class SourceKind(str, Enum):
    REPLAY = "replay"   # rotulo na interface: Replay
    MOCK = "mock"       # rotulo na interface: Simulado
    LIVE = "live"       # rotulo na interface: Desconectado (nao implementado)

    @property
    def label(self) -> str:
        return {"replay": "Replay", "mock": "Simulado", "live": "Desconectado"}[self.value]


def create_adapter(kind: SourceKind, path: Optional[Path] = None) -> GameAdapter:
    if kind is SourceKind.REPLAY:
        if path is None:
            raise ValueError("replay exige um arquivo .jsonl")
        from .replay import ReplayAdapter
        return ReplayAdapter.from_file(path)
    if kind is SourceKind.MOCK:
        if path is None:
            raise ValueError("simulado exige um cenario .json")
        from .mock import MockAdapter
        return MockAdapter.from_scenario(path)
    from .live import LiveAdapter
    return LiveAdapter()
