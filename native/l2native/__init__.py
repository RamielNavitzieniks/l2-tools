"""L2-Tools Native: motor independente de scripts Python para Lineage II.

Esta versao executa somente sobre fontes offline (replay, simulacao) e
observacao visual opcional. Nao ha integracao com o cliente Live.
"""
from .actions import ActionKind, ActionResult, Intent, ResultStatus
from .engine import Bot, Engine, EngineConfig, EngineStatus, RunReport
from .journal import Journal
from .model import Entity, Fact, FactStatus, GameState, Position, inferred, observed, unknown
from .script import LoadedScript, ScriptLoadError, load_script

__version__ = "0.1.0"

__all__ = [
    "ActionKind", "ActionResult", "Intent", "ResultStatus",
    "Bot", "Engine", "EngineConfig", "EngineStatus", "RunReport",
    "Journal", "Entity", "Fact", "FactStatus", "GameState", "Position",
    "inferred", "observed", "unknown", "LoadedScript", "ScriptLoadError", "load_script",
]
