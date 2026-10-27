"""Utilitarios de teste: scripts em memoria e acesso ao diretorio do pacote."""
from __future__ import annotations

import types
from pathlib import Path

from l2native.script import LoadedScript, from_module

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "fixtures"


def make_script(on_tick, **hooks) -> LoadedScript:
    module = types.ModuleType("test_script")
    module.on_tick = on_tick
    for name, fn in hooks.items():
        setattr(module, name, fn)
    return from_module(module)


def results(engine, status=None):
    out = [r.data for r in engine.journal.events("result")]
    return [r for r in out if status is None or r["status"] == status]
