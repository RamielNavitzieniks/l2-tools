"""Host de scripts Python nativos do l2-tools, com observacoes JSONL OFFLINE.

Sem conexao com Lineage II, clientes ou Adrenaline.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Context:
    state: dict[str, Any]
    intents: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def propose(self, kind: str, **parameters: Any) -> None:
        """Registra intencoes. NAO envia nenhuma acao ao jogo."""
        if not isinstance(kind, str) or not kind:
            raise ValueError("A intencao deve ter tipo textual nao vazio")
        self.intents.append({"kind": kind, "parameters": parameters})

    def note(self, message: str) -> None:
        self.notes.append(str(message))


def load_python_script(path: Path) -> Callable[[Context], None]:
    """Carrega um arquivo .py confiavel e localiza on_tick(context).

    ATENCAO: nao e sandbox; modulos Python executam codigo no import.
    """
    path = path.resolve(strict=True)
    if path.suffix.lower() != ".py":
        raise ValueError("Esperado script .py")
    spec = importlib.util.spec_from_file_location("l2tools_user_script", path)
    if spec is None or spec.loader is None:
        raise ValueError("Falha ao carregar modulo Python")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, "on_tick", None)
    if not callable(handler):
        raise ValueError("Script precisa definir on_tick(context)")
    return handler


def replay(script_path: Path, events_path: Path) -> list[dict[str, Any]]:
    handler = load_python_script(script_path)
    results: list[dict[str, Any]] = []
    with events_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            state = json.loads(line)
            if not isinstance(state, dict):
                raise ValueError(f"Linha {line_number}: estado deve ser objeto")
            ctx = Context(state=state)
            handler(ctx)
            results.append({"seq": line_number, "notes": ctx.notes, "intents": ctx.intents})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Executar scripts Python sobre eventos OFFLINE"
    )
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    args = parser.parse_args()
    for result in replay(args.script, args.events):
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
