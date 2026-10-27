"""Linha de comando: python -m l2native run --script ... --adapter ..."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .adapters.base import AdapterUnavailable, GameAdapter
from .adapters.factory import SourceKind, create_adapter
from .engine import Engine, EngineConfig, EngineStatus
from .journal import Journal, Record
from .script import ScriptLoadError, load_script

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_UNAVAILABLE = 0, 1, 2, 3


def _param(text: str) -> tuple[str, Any]:
    key, sep, raw = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError("use CHAVE=VALOR")
    try:
        return key, json.loads(raw)
    except json.JSONDecodeError:
        return key, raw


def _echo(record: Record) -> None:
    d = record.to_dict()
    e = record.event
    if e == "observation":
        return
    if e == "intent":
        line = f"intencao  #{d['intent']['id']} {d['intent']['kind']} {d['intent']['params']}"
    elif e == "result":
        line = f"resultado #{d['intent']['id']} {d['intent']['kind']} -> {d['status'].upper()}" + (
            f" ({d['detail']})" if d.get("detail") else "")
    elif e == "script_log":
        line = f"script    {d['message']}"
    else:
        extra = {k: v for k, v in d.items() if k not in ("seq", "tick", "event", "traceback")}
        line = f"{e:<9} {extra}" if extra else e
    print(f"[t{record.tick:04d}] {line}")


def _build_adapter(args: argparse.Namespace) -> GameAdapter:
    if args.adapter == "replay":
        if not args.events:
            raise SystemExit("--adapter replay exige --events ARQUIVO.jsonl")
        return create_adapter(SourceKind.REPLAY, args.events)
    if args.adapter == "mock":
        if not args.scenario:
            raise SystemExit("--adapter mock exige --scenario ARQUIVO.json")
        return create_adapter(SourceKind.MOCK, args.scenario)
    if args.adapter == "visual":
        if not args.template or not (args.image or args.hwnd):
            raise SystemExit("--adapter visual exige --template e (--image ... ou --hwnd N)")
        from .adapters.visual import VisualAdapter
        from .vision import SequenceCapture, TemplateDetector, WindowClientCapture
        detector = TemplateDetector.from_file(args.template, threshold=args.threshold,
                                              scales=tuple(args.scale or (1.0,)))
        if args.image:
            return VisualAdapter(SequenceCapture(args.image), detector)
        return VisualAdapter(WindowClientCapture(args.hwnd), detector, samples=2)
    return create_adapter(SourceKind.LIVE)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="l2native", description="Motor Python nativo do L2-Tools (offline)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="executar um script Python")
    run.add_argument("--script", type=Path, required=True)
    run.add_argument("--adapter", choices=("replay", "mock", "visual", "live"), required=True)
    run.add_argument("--events", type=Path, help="JSONL para --adapter replay")
    run.add_argument("--scenario", type=Path, help="JSON para --adapter mock")
    run.add_argument("--template", type=Path, help="modelo PNG para --adapter visual")
    run.add_argument("--image", type=Path, action="append", help="PNG offline (repetivel) para --adapter visual")
    run.add_argument("--hwnd", type=int, help="janela autorizada (Windows) para --adapter visual")
    run.add_argument("--threshold", type=float, default=0.91)
    run.add_argument("--scale", type=float, action="append", help="escala do modelo (repetivel)")
    run.add_argument("--param", type=_param, action="append", default=[], help="CHAVE=VALOR para bot.params")
    run.add_argument("--max-ticks", type=int)
    run.add_argument("--interval", type=float, default=0.0, help="segundos entre ciclos")
    run.add_argument("--journal", type=Path, help="gravar registro completo em JSONL")
    run.add_argument("--quiet", action="store_true")
    sub.add_parser("gui", help="abrir a interface grafica (requer PySide6)")
    args = parser.parse_args(argv)
    if args.command == "gui":
        from .gui.app import main as gui_main
        return gui_main([])

    try:
        script = load_script(args.script)
    except ScriptLoadError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        adapter = _build_adapter(args)
    except AdapterUnavailable as exc:
        print(f"INDISPONIVEL: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERRO ao preparar adaptador: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE

    sink = args.journal.open("w", encoding="utf-8") if args.journal else None
    try:
        journal = Journal(sink=sink, echo=None if args.quiet else _echo)
        engine = Engine(adapter, script, journal=journal, params=dict(args.param),
                        config=EngineConfig(max_ticks=args.max_ticks, tick_interval=args.interval))
        report = engine.run()
    finally:
        if sink:
            sink.close()
    counts: dict[str, int] = {}
    for r in report.journal.events("result"):
        counts[r.data["status"]] = counts.get(r.data["status"], 0) + 1
    print(f"FIM: {report.status.value} ({report.reason}); ciclos={report.ticks}; resultados={counts}")
    if report.status is EngineStatus.FAILED:
        return EXIT_UNAVAILABLE if report.reason.startswith("adaptador indisponivel") else EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
