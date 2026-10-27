"""Formatacao compartilhada pela GUI (sem dependencia de Qt, testavel)."""
from __future__ import annotations

from typing import Any

from ..journal import Record
from ..model import Fact, FactStatus, Position

UNKNOWN_TEXT = "desconhecido"

CATEGORIES = {
    "intent": "acoes", "result": "acoes", "intents_discarded": "acoes",
    "script_error": "erros", "adapter_error": "erros", "adapter_crash": "erros", "session_error": "erros",
    "plugin_error": "erros",
    "paused": "pausas", "resumed": "pausas",
    "script_log": "script", "script_output": "script",
    "engine_start": "motor", "engine_stop": "motor", "observation": "observacoes",
    "plugin_log": "plugins", "plugin_installed": "plugins", "plugin_enabled": "plugins",
    "plugin_disabled": "plugins", "plugin_removed": "plugins", "plugin_slow": "plugins",
}

FILTERS = (
    ("Todos (sem observacoes)", None),
    ("Acoes e resultados", "acoes"),
    ("Erros", "erros"),
    ("Pausas", "pausas"),
    ("Script", "script"),
    ("Motor", "motor"),
    ("Plugins", "plugins"),
    ("Observacoes", "observacoes"),
)


def value_text(value: Any) -> str:
    if isinstance(value, Position):
        return f"{value.x:.0f}, {value.y:.0f}, {value.z:.0f}"
    if isinstance(value, bool):
        return "sim" if value else "nao"
    if isinstance(value, float):
        return f"{value:.3g}"
    if isinstance(value, tuple):
        return f"{len(value)} item(ns)"
    return str(value)


def fact_text(fact: Fact, percent: bool = False) -> str:
    """'valor (observado; fonte; conf 0.97)' ou 'desconhecido (motivo)'."""
    if not fact.known:
        return UNKNOWN_TEXT + (f" ({fact.note})" if fact.note else "")
    value = f"{fact.value * 100:.0f}%" if percent and isinstance(fact.value, (int, float)) else value_text(fact.value)
    meta = ["observado" if fact.status is FactStatus.OBSERVED else "inferido"]
    if fact.source:
        meta.append(fact.source)
    if fact.confidence is not None:
        meta.append(f"conf {fact.confidence:.2f}")
    return f"{value} ({'; '.join(meta)})"


def describe(record: Record) -> str:
    d = record.data
    e = record.event
    if e == "intent":
        i = d["intent"]
        return f"intencao #{i['id']} {i['kind']} {i['params']}"
    if e == "result":
        i = d["intent"]
        text = f"#{i['id']} {i['kind']} -> {d['status'].upper()}"
        return text + (f" ({d['detail']})" if d.get("detail") else "")
    if e == "script_error":
        where = f" linha {d['line']}" if d.get("line") else ""
        return f"{d.get('hook')}{where}: {d['error']}"
    if e == "observation":
        known = [k for k, v in d["state"].items() if isinstance(v, dict)]
        return "campos conhecidos: " + (", ".join(known) or "nenhum")
    if e == "script_log":
        extra = {k: v for k, v in d.items() if k != "message"}
        return d["message"] + (f" {extra}" if extra else "")
    if e == "script_output":
        return d["text"]
    if e == "plugin_log":
        return f"[{d['plugin']}] {d['message']}"
    return ", ".join(f"{k}={v}" for k, v in d.items() if k != "traceback")
