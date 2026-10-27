"""Sessao: um motor rodando em thread propria, controlavel por GUI ou testes.

O Core continua o mesmo do CLI; a sessao so acrescenta a thread de trabalho,
o estado de alto nivel e a distribuicao de eventos. Ouvintes sao chamados NA
THREAD DO MOTOR: a GUI os encaminha para a thread Qt por sinais.
"""
from __future__ import annotations

import threading
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from .adapters.factory import SourceKind, create_adapter
from .engine import Engine, EngineConfig, EngineStatus, RunReport
from .journal import Journal, Record
from .script import ScriptLoadError, load_script


class SessionStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


Listener = Callable[[str, Any], None]  # ("record", Record) ou ("status", SessionStatus)


class Session:
    def __init__(self, source: SourceKind, source_path: Optional[Path], script_path: Path, *,
                 params: Optional[dict[str, Any]] = None, config: EngineConfig = EngineConfig(tick_interval=0.2)) -> None:
        self.source = source
        self.source_path = source_path
        self.script_path = Path(script_path)
        self.params = dict(params or {})
        self.config = replace(config, capture_script_output=True)
        self.status = SessionStatus.IDLE
        self.engine: Optional[Engine] = None
        self.report: Optional[RunReport] = None
        self.error: str = ""
        self._listeners: list[Listener] = []
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _emit(self, kind: str, payload: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:  # um ouvinte com defeito nao derruba o motor
                pass

    def _set_status(self, status: SessionStatus) -> None:
        with self._lock:
            if self.status is status:
                return
            self.status = status
        self._emit("status", status)

    def _on_record(self, record: Record) -> None:
        self._emit("record", record)
        if record.event == "paused":
            self._set_status(SessionStatus.PAUSED)
        elif record.event == "resumed" and self.status is not SessionStatus.STOPPING:
            self._set_status(SessionStatus.RUNNING)

    def _fail(self, message: str) -> bool:
        self.error = message
        self._emit("record", Record(0, 0, "session_error", {"error": message}))
        self._set_status(SessionStatus.FAILED)
        return False

    # --- controles -----------------------------------------------------------
    def start(self) -> bool:
        """Prepara script e adaptador nesta thread (erros aparecem na hora) e
        inicia o ciclo na thread de trabalho."""
        if self.status is not SessionStatus.IDLE:
            raise RuntimeError("sessao ja iniciada; crie outra para executar de novo")
        try:
            script = load_script(self.script_path)
        except ScriptLoadError as exc:
            return self._fail(str(exc))
        try:
            adapter = create_adapter(self.source, self.source_path)
        except (OSError, ValueError, RuntimeError) as exc:
            return self._fail(f"fonte de dados: {exc}")
        self.engine = Engine(adapter, script, config=self.config, journal=Journal(echo=self._on_record),
                             params=self.params)
        self._set_status(SessionStatus.RUNNING)
        self._thread = threading.Thread(target=self._run, name="l2native-engine", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        assert self.engine is not None
        try:
            self.report = self.engine.run()
        except Exception as exc:  # defeito no proprio motor: registrar, nunca mascarar
            self._fail(f"falha interna do motor: {type(exc).__name__}: {exc}")
            return
        ok = self.report.status is EngineStatus.STOPPED
        self._set_status(SessionStatus.STOPPED if ok else SessionStatus.FAILED)

    def pause(self, reason: str = "pausa solicitada pelo operador") -> None:
        if self.engine and self.status is SessionStatus.RUNNING:
            self.engine.request_pause(reason)

    def resume(self) -> None:
        """Retoma pausa do operador ou confirma intervencao manual (quest)."""
        if self.engine and self.status is SessionStatus.PAUSED:
            self.engine.request_resume()

    def stop(self) -> None:
        if self.engine and self.status in (SessionStatus.RUNNING, SessionStatus.PAUSED):
            self._set_status(SessionStatus.STOPPING)
            self.engine.cancel("parado pelo operador")
            self.engine.request_resume()  # acorda a espera da pausa para encerrar ja

    def join(self, timeout: Optional[float] = None) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    @property
    def finished(self) -> bool:
        return self.status in (SessionStatus.STOPPED, SessionStatus.FAILED)
