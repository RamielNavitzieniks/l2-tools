"""Registro estruturado de decisoes e resultados (JSONL opcional)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TextIO


@dataclass(frozen=True)
class Record:
    seq: int
    tick: int
    event: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "tick": self.tick, "event": self.event, **self.data}


class Journal:
    def __init__(self, sink: Optional[TextIO] = None, echo: Optional[Callable[[Record], None]] = None) -> None:
        self.records: list[Record] = []
        self._sink = sink
        self._echo = echo

    def write(self, tick: int, event: str, **data: Any) -> Record:
        record = Record(len(self.records) + 1, tick, event, data)
        self.records.append(record)
        if self._sink is not None:
            self._sink.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")
            self._sink.flush()
        if self._echo is not None:
            self._echo(record)
        return record

    def events(self, name: str) -> list[Record]:
        return [r for r in self.records if r.event == name]
