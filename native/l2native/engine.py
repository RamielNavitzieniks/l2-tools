"""Motor de execucao: observar -> script decide -> registrar intencoes ->
validar -> encaminhar ao adaptador -> registrar resultados -> proximo ciclo.

Garantias:
- erro no script descarta as intencoes daquele ciclo (nunca sao executadas);
- acao sem backend no adaptador vira UNSUPPORTED sem chegar ao adaptador;
- excecao ou retorno invalido do adaptador vira FAILED;
- pausa/parada/cancelamento interrompem as intencoes seguintes do ciclo;
- toda intencao e todo resultado ficam no Journal.
"""
from __future__ import annotations

import contextlib
import io
import threading
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Optional

from .actions import ENGINE_ACTIONS, ActionKind, ActionResult, Intent, ResultStatus, validate_params
from .adapters.base import AdapterError, AdapterUnavailable, GameAdapter, SourceExhausted
from .geometry import distance_xy
from .journal import Journal
from .model import GameState, Position
from .perception import Perception
from .script import LoadedScript


class EngineStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"   # encerramento controlado
    FAILED = "failed"     # encerramento por falha


@dataclass(frozen=True)
class EngineConfig:
    max_ticks: Optional[int] = None
    tick_interval: float = 0.0            # segundos entre ciclos (0 = replay o mais rapido possivel)
    max_consecutive_script_errors: int = 1
    max_consecutive_adapter_errors: int = 3
    max_move_distance: float = 5000.0     # recusa move_to mais longe que isso da posicao observada
    resume_stable_ticks: int = 2          # ciclos seguidos com dialogo observado fechado para retomar
    pause_poll_interval: float = 0.05     # espera por ciclo durante pausa do operador (sem observar)
    capture_script_output: bool = False   # print()/stderr do script viram registros 'script_output'


@dataclass(frozen=True)
class RunReport:
    status: EngineStatus
    reason: str
    ticks: int
    journal: Journal


class Actions:
    """bot.actions: cada metodo apenas ENFILEIRA uma intencao e a devolve."""

    def __init__(self, enqueue: Callable[[ActionKind, dict[str, Any]], Intent]) -> None:
        self._enqueue = enqueue

    def move_to(self, x: float, y: float, z: float) -> Intent:
        return self._enqueue(ActionKind.MOVE_TO, {"x": x, "y": y, "z": z})

    def move_to_position(self, p: Position) -> Intent:
        return self.move_to(p.x, p.y, p.z)

    def follow(self, character: str, distance: float) -> Intent:
        return self._enqueue(ActionKind.FOLLOW, {"character": character, "distance": distance})

    def select_target(self, name: Optional[str] = None, npc_id: Optional[int] = None) -> Intent:
        params = {k: v for k, v in (("name", name), ("npc_id", npc_id)) if v is not None}
        return self._enqueue(ActionKind.SELECT_TARGET, params)

    def attack(self) -> Intent:
        return self._enqueue(ActionKind.ATTACK, {})

    def use_skill(self, skill_id: int) -> Intent:
        return self._enqueue(ActionKind.USE_SKILL, {"skill_id": skill_id})

    def teleport(self, destination_id: int) -> Intent:
        return self._enqueue(ActionKind.TELEPORT, {"destination_id": destination_id})

    def pause(self, reason: str, resume_on: str = "dialog_closed") -> Intent:
        return self._enqueue(ActionKind.PAUSE, {"reason": reason, "resume_on": resume_on})

    def stop(self, reason: str) -> Intent:
        return self._enqueue(ActionKind.STOP, {"reason": reason})


class Bot:
    """Objeto recebido pelos scripts. `memory` persiste entre ciclos e pausas."""

    def __init__(self, engine: "Engine", params: dict[str, Any]) -> None:
        self._engine = engine
        self.params = dict(params)
        self.memory: dict[str, Any] = {}
        self.actions = Actions(engine._enqueue)
        self.perception = Perception(GameState(tick=0))
        self.tick = 0
        self.last_results: tuple[ActionResult, ...] = ()

    @property
    def capabilities(self) -> frozenset[ActionKind]:
        return self._engine.capabilities

    def log(self, message: str, **data: Any) -> None:
        self._engine.journal.write(self.tick, "script_log", message=str(message), **data)

    def pause_for_manual_interaction(self, reason: str = "intervencao manual necessaria",
                                     resume_on: str = "dialog_closed") -> Intent:
        return self.actions.pause(reason, resume_on)

    def pause_if_quest_dialog(self) -> bool:
        """Atalho do escopo vigente: missoes sao manuais. Pausa se a janela de
        missao foi vista (observada/inferida). Desconhecido NAO pausa nem libera."""
        if self.perception.quest_dialog_visible():
            self.pause_for_manual_interaction("janela de missao visivel: aceitar/entregar manualmente")
            return True
        return False

    def stop(self, reason: str) -> Intent:
        return self.actions.stop(reason)


def _summary(state: GameState) -> dict[str, Any]:
    full = state.to_dict()
    return {k: v for k, v in full.items() if not isinstance(v, dict) or v.get("status") != "unknown"}


class Engine:
    def __init__(self, adapter: GameAdapter, script: LoadedScript, *, config: EngineConfig = EngineConfig(),
                 journal: Optional[Journal] = None, params: Optional[dict[str, Any]] = None) -> None:
        self.adapter = adapter
        self.script = script
        self.config = config
        self.journal = journal or Journal()
        self.status = EngineStatus.CREATED
        self.reason = ""
        self.ticks = 0
        self.capabilities: frozenset[ActionKind] = frozenset()
        self.bot = Bot(self, params or {})
        self._queue: list[Intent] = []
        self._next_intent = 1
        self._cancel = threading.Event()
        self._cancel_reason = ""
        self._resume_requested = threading.Event()
        self._pause: Optional[dict[str, Any]] = None
        self._pause_request: Optional[str] = None
        self._script_errors = 0
        self._adapter_errors = 0
        self._finished = False

    # --- controle externo (thread-safe) -------------------------------------
    def cancel(self, reason: str = "cancelado pelo operador") -> None:
        self._cancel_reason = reason
        self._cancel.set()

    def request_resume(self) -> None:
        """Retomada explicita pelo operador apos intervencao manual."""
        self._resume_requested.set()

    def request_pause(self, reason: str = "pausa solicitada pelo operador") -> None:
        """Pausa no inicio do proximo ciclo; so o operador retoma. Enquanto
        durar, o motor nao observa (um replay fica congelado, nao e consumido)."""
        self._pause_request = reason

    # --- ciclo ---------------------------------------------------------------
    def start(self) -> bool:
        if self.status is not EngineStatus.CREATED:
            raise RuntimeError("motor ja iniciado")
        self.capabilities = frozenset(self.adapter.capabilities())
        self.status = EngineStatus.RUNNING
        observes = getattr(self.adapter, "observes", None)
        declared = observes() if callable(observes) else None
        self.journal.write(0, "engine_start", script=self.script.name, adapter=self.adapter.name,
                           capabilities=sorted(k.value for k in self.capabilities),
                           observes=None if declared is None else sorted(declared))
        if self.script.on_start and not self._call_script(self.script.on_start, "on_start"):
            self._finish(EngineStatus.FAILED, "erro em on_start")
            return False
        return True

    def run(self) -> RunReport:
        try:
            if self.start():
                while self.step():
                    if self.config.tick_interval > 0:
                        self._cancel.wait(self.config.tick_interval)
        except KeyboardInterrupt:
            self._finish(EngineStatus.STOPPED, "interrompido pelo operador (Ctrl+C)")
        return RunReport(self.status, self.reason, self.ticks, self.journal)

    def step(self) -> bool:
        """Executa um ciclo. Devolve False quando o motor encerrou."""
        if self._finished:
            return False
        if self._cancel.is_set():
            return self._finish(EngineStatus.STOPPED, self._cancel_reason)
        if self.config.max_ticks is not None and self.ticks >= self.config.max_ticks:
            return self._finish(EngineStatus.STOPPED, "limite de ciclos atingido")
        if self._pause_request is not None and self.status is EngineStatus.RUNNING:
            self._enter_pause(self._pause_request, "operator")
            self._pause_request = None
            return True
        if (self.status is EngineStatus.PAUSED and self._pause["resume_on"] == "operator"
                and not self._resume_requested.is_set()):
            self._resume_requested.wait(self.config.pause_poll_interval)
            return True

        started = time.perf_counter()
        state = self._observe()
        if state is None:
            return not self._finished
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        self.ticks += 1
        self.bot.tick = state.tick
        self.bot.perception = Perception(state)
        self.journal.write(state.tick, "observation", state=_summary(state), latency_ms=latency_ms)

        self._queue = []  # on_resume e on_tick do mesmo ciclo compartilham a fila
        if self.status is EngineStatus.PAUSED and not self._try_resume(state):
            self._queue = []
            return True

        if not self._call_script(self.script.on_tick, "on_tick"):
            discarded = [i.to_dict() for i in self._queue]
            self._queue = []
            if discarded:
                self.journal.write(self.bot.tick, "intents_discarded", reason="erro no script", intents=discarded)
            if self._script_errors >= self.config.max_consecutive_script_errors:
                return self._finish(EngineStatus.FAILED, "erro no script")
            return True
        self._script_errors = 0
        return self._process(self._queue, state)

    def _observe(self) -> Optional[GameState]:
        try:
            state = self.adapter.observe()
        except SourceExhausted as exc:
            self._finish(EngineStatus.STOPPED, f"fonte encerrada: {exc}")
            return None
        except AdapterUnavailable as exc:
            self._finish(EngineStatus.FAILED, f"adaptador indisponivel: {exc}")
            return None
        except Exception as exc:  # AdapterError ou falha inesperada do adaptador
            self._adapter_errors += 1
            kind = "adapter_error" if isinstance(exc, AdapterError) else "adapter_crash"
            self.journal.write(self.bot.tick, kind, error=f"{type(exc).__name__}: {exc}",
                               consecutive=self._adapter_errors)
            if self._adapter_errors >= self.config.max_consecutive_adapter_errors:
                self._finish(EngineStatus.FAILED, "falhas consecutivas do adaptador")
            return None
        if not isinstance(state, GameState):
            self._adapter_errors += 1
            self.journal.write(self.bot.tick, "adapter_error", error="observe() nao devolveu GameState",
                               consecutive=self._adapter_errors)
            if self._adapter_errors >= self.config.max_consecutive_adapter_errors:
                self._finish(EngineStatus.FAILED, "falhas consecutivas do adaptador")
            return None
        self._adapter_errors = 0
        return state

    def _call_script(self, fn: Callable[..., Any], hook: str, *args: Any) -> bool:
        out = io.StringIO() if self.config.capture_script_output else None
        try:
            with (contextlib.redirect_stdout(out) if out else contextlib.nullcontext()), \
                    (contextlib.redirect_stderr(out) if out else contextlib.nullcontext()):
                fn(self.bot, *args)
            return True
        except Exception as exc:
            self._script_errors += 1
            self.journal.write(self.bot.tick, "script_error", hook=hook, error=f"{type(exc).__name__}: {exc}",
                               line=self._script_line(exc),
                               traceback=traceback.format_exc(limit=5).splitlines()[-6:],
                               consecutive=self._script_errors)
            return False
        finally:
            if out is not None and out.getvalue():
                self.journal.write(self.bot.tick, "script_output", hook=hook, text=out.getvalue().rstrip("\n"))

    def _script_line(self, exc: BaseException) -> Optional[int]:
        """Linha do arquivo do script onde a excecao ocorreu (a mais interna)."""
        if self.script.path is None:
            return None
        target = str(self.script.path)
        line = None
        for frame in traceback.extract_tb(exc.__traceback__):
            if frame.filename == target:
                line = frame.lineno
        return line

    def _enqueue(self, kind: ActionKind, params: dict[str, Any]) -> Intent:
        intent = Intent(self._next_intent, kind, dict(params))
        self._next_intent += 1
        self._queue.append(intent)
        return intent

    # --- acoes ---------------------------------------------------------------
    def _process(self, intents: Iterable[Intent], state: GameState) -> bool:
        intents = list(intents)
        for intent in intents:
            self.journal.write(state.tick, "intent", intent=intent.to_dict())
        results: list[ActionResult] = []
        halt: Optional[str] = None
        stop_reason: Optional[str] = None
        for intent in intents:
            if halt is None and self._cancel.is_set():
                halt = "cancelamento"
            if halt is not None:
                result = ActionResult(intent, ResultStatus.INTERRUPTED, f"nao enviada: {halt} no mesmo ciclo")
            else:
                result = self._resolve(intent, state)
                if result.status.succeeded and intent.kind is ActionKind.PAUSE:
                    halt = "pausa"
                elif result.status.succeeded and intent.kind is ActionKind.STOP:
                    halt = "parada"
                    stop_reason = intent.params["reason"]
            results.append(result)
            self.journal.write(state.tick, "result", **result.to_dict())
            if halt == "pausa" and intent.kind is ActionKind.PAUSE and result.status.succeeded:
                self._enter_pause(intent.params["reason"], intent.params.get("resume_on", "dialog_closed"))
        self.bot.last_results = tuple(results)
        if stop_reason is not None:
            return self._finish(EngineStatus.STOPPED, f"script solicitou parada: {stop_reason}")
        return True

    def _resolve(self, intent: Intent, state: GameState) -> ActionResult:
        errors = validate_params(intent.kind, intent.params)
        if errors:
            return ActionResult(intent, ResultStatus.REJECTED, "; ".join(errors))
        if intent.kind in ENGINE_ACTIONS:
            return ActionResult(intent, ResultStatus.COMPLETED, f"tratada pelo motor ({intent.kind.value})")
        if intent.kind not in self.capabilities:
            return ActionResult(intent, ResultStatus.UNSUPPORTED,
                                f"adaptador '{self.adapter.name}' nao tem backend validado para {intent.kind.value}")
        policy = self._policy(intent, state)
        if policy:
            return ActionResult(intent, ResultStatus.REJECTED, policy)
        try:
            result = self.adapter.execute(intent)
        except Exception as exc:
            return ActionResult(intent, ResultStatus.FAILED, f"excecao no adaptador: {type(exc).__name__}: {exc}")
        if not isinstance(result, ActionResult) or result.intent != intent:
            return ActionResult(intent, ResultStatus.FAILED, f"adaptador devolveu resultado invalido: {result!r}")
        return result

    def _policy(self, intent: Intent, state: GameState) -> Optional[str]:
        if intent.kind is ActionKind.MOVE_TO and state.self_position.known:
            p = intent.params
            gap = distance_xy(state.self_position.value, Position(p["x"], p["y"], p["z"]))
            if gap > self.config.max_move_distance:
                return f"destino a {gap:.0f} unidades excede max_move_distance={self.config.max_move_distance:.0f}"
        return None

    # --- pausa ---------------------------------------------------------------
    @property
    def pause_info(self) -> Optional[dict[str, Any]]:
        return dict(self._pause) if self._pause else None

    def _enter_pause(self, reason: str, resume_on: str) -> None:
        self.status = EngineStatus.PAUSED
        self._resume_requested.clear()
        self._pause = {"reason": reason, "resume_on": resume_on, "since_tick": self.bot.tick, "clear_ticks": 0}
        self.journal.write(self.bot.tick, "paused", reason=self._pause["reason"], resume_on=self._pause["resume_on"])

    def _try_resume(self, state: GameState) -> bool:
        assert self._pause is not None
        how = None
        if self._resume_requested.is_set():
            how = "operador"
        elif self._pause["resume_on"] == "dialog_closed":
            # Desconhecido NAO conta como fechado e zera a contagem.
            self._pause["clear_ticks"] = self._pause["clear_ticks"] + 1 if state.quest_dialog.is_false() else 0
            if self._pause["clear_ticks"] >= self.config.resume_stable_ticks:
                how = "dialogo observado fechado"
        if how is None:
            return False
        self._resume_requested.clear()
        self.journal.write(state.tick, "resumed", by=how, paused_since=self._pause["since_tick"])
        self._pause = None
        self.status = EngineStatus.RUNNING
        if self.script.on_resume and not self._call_script(self.script.on_resume, "on_resume"):
            if self._script_errors >= self.config.max_consecutive_script_errors:
                self._finish(EngineStatus.FAILED, "erro em on_resume")
            return False
        return True

    # --- encerramento --------------------------------------------------------
    def _finish(self, status: EngineStatus, reason: str) -> bool:
        if self._finished:
            return False
        self._finished = True
        self.status, self.reason = status, reason
        if self.script.on_stop:
            self._call_script(self.script.on_stop, "on_stop", reason)
        try:
            self.adapter.close()
        except Exception as exc:
            self.journal.write(self.bot.tick, "adapter_error", error=f"close(): {type(exc).__name__}: {exc}")
        self.journal.write(self.bot.tick, "engine_stop", status=status.value, reason=reason, ticks=self.ticks)
        return False
