"""Carregamento e validacao de scripts Python do usuario.

ATENCAO: scripts sao codigo confiavel executado SEM sandbox, no mesmo
processo e com as permissoes do usuario. Carregue apenas arquivos seus.

Contrato minimo:
    def on_tick(bot): ...          obrigatorio, chamado a cada ciclo ativo
Opcionais:
    def on_start(bot): ...         antes do primeiro ciclo
    def on_resume(bot): ...        ao sair de uma pausa
    def on_stop(bot, reason): ...  ao encerrar (qualquer motivo)
"""
from __future__ import annotations

import importlib.util
import inspect
import itertools
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional

_counter = itertools.count(1)

_HOOK_ARITY = {"on_tick": 1, "on_start": 1, "on_resume": 1, "on_stop": 2}


class ScriptLoadError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedScript:
    name: str
    path: Optional[Path]
    module: ModuleType
    on_tick: Callable[[Any], Any]
    on_start: Optional[Callable[[Any], Any]] = None
    on_resume: Optional[Callable[[Any], Any]] = None
    on_stop: Optional[Callable[[Any, str], Any]] = None


def _accepts(fn: Callable[..., Any], count: int) -> bool:
    try:
        inspect.signature(fn).bind(*([None] * count))
        return True
    except TypeError:
        return False
    except ValueError:  # sem assinatura introspectavel (builtins): aceitar
        return True


def from_module(module: ModuleType, path: Optional[Path] = None) -> LoadedScript:
    hooks: dict[str, Any] = {}
    for hook, arity in _HOOK_ARITY.items():
        fn = getattr(module, hook, None)
        if fn is None:
            if hook == "on_tick":
                raise ScriptLoadError("script precisa definir on_tick(bot)")
            continue
        if not callable(fn):
            raise ScriptLoadError(f"{hook} deve ser funcao")
        if not _accepts(fn, arity):
            args = "bot, reason" if arity == 2 else "bot"
            raise ScriptLoadError(f"{hook} deve aceitar ({args})")
        hooks[hook] = fn
    name = path.stem if path else module.__name__
    return LoadedScript(name=name, path=path, module=module, **hooks)


def load_script(path: Path | str) -> LoadedScript:
    path = Path(path)
    if path.suffix.lower() != ".py":
        raise ScriptLoadError(f"esperado arquivo .py: {path}")
    if not path.is_file():
        raise ScriptLoadError(f"script nao encontrado: {path}")
    path = path.resolve()
    module_name = f"l2native_user_script_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ScriptLoadError(f"nao foi possivel preparar o modulo: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # necessario para dataclasses/pickle no script
    try:
        spec.loader.exec_module(module)
        return from_module(module, path)
    except ScriptLoadError:
        sys.modules.pop(module_name, None)
        raise
    except BaseException as exc:  # SyntaxError, ImportError, erro no corpo do modulo
        sys.modules.pop(module_name, None)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ScriptLoadError(f"erro ao importar {path.name}: {type(exc).__name__}: {exc}") from exc
