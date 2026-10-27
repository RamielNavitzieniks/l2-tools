"""Plugin de exemplo: conta eventos do journal. Nao acessa o jogo nem a rede."""
from collections import Counter

_api = None
counts = Counter()


def activate(api):
    global _api
    _api = api
    counts.clear()
    api.log("contador de eventos ativo")


def on_record(record):
    counts[record.event] += 1
    if record.event == "engine_stop":
        _api.log("resumo: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def deactivate():
    counts.clear()
