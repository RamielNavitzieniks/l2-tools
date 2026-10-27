"""Conjunto minimo de observacoes exigido antes de conectar um adaptador real
(registro 022). Ver docs/contratos/observacao-minima.md.

O `ObservationMonitor` mede, numa janela recente, se a fonte entrega cada
requisito com proveniencia, e cruza com o que o adaptador DECLARA observar
(`adapter.observes()`). Declaracao sem evidencia nao conta como atendida.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .model import GameState


class Requirement(str, Enum):
    SELF_POSITION = "posicao_propria"
    NEARBY_ENTITIES = "entidades_proximas"
    VITALS = "hp_mp"
    TARGET = "alvo"
    PROVENANCE = "proveniencia"
    ADAPTER_HEALTH = "saude_do_adaptador"


#: campos do GameState exigidos por requisito (todos precisam estar conhecidos)
FIELDS: dict[Requirement, tuple[str, ...]] = {
    Requirement.SELF_POSITION: ("self_position",),
    Requirement.NEARBY_ENTITIES: ("characters", "npcs"),
    Requirement.VITALS: ("hp", "mp"),
    Requirement.TARGET: ("target",),
}

WHY = {
    Requirement.SELF_POSITION: "XYZ propria: base de movimento, rotas, zona e seguimento.",
    Requirement.NEARBY_ENTITIES: "Jogadores e NPCs proximos (lista vazia observada != desconhecida): alvo, area ocupada.",
    Requirement.VITALS: "HP e MP: recuperacao, morte, condicoes de regras.",
    Requirement.TARGET: "Alvo atual: combate e confirmacao de select_target.",
    Requirement.PROVENANCE: "Todo valor conhecido precisa de fonte; confianca quando a fonte e probabilistica.",
    Requirement.ADAPTER_HEALTH: "Erros e latencia de observacao dentro de limites, para decisoes nao obsoletas.",
}


class ReadinessStatus(str, Enum):
    MET = "atendido"
    NOT_MET = "nao atendido"
    INSUFFICIENT = "dados insuficientes"


@dataclass(frozen=True)
class Readiness:
    requirement: Requirement
    status: ReadinessStatus
    detail: str
    declared: Optional[bool] = None   # None = adaptador nao declara / nao se aplica


class ObservationMonitor:
    def __init__(self, window: int = 50, min_samples: int = 10, coverage: float = 0.9,
                 max_error_rate: float = 0.05, max_p95_latency_ms: float = 250.0) -> None:
        self.window, self.min_samples, self.coverage = window, min_samples, coverage
        self.max_error_rate, self.max_p95_latency_ms = max_error_rate, max_p95_latency_ms
        self.states: deque[GameState] = deque(maxlen=window)
        self.latencies: deque[float] = deque(maxlen=window)
        self.outcomes: deque[bool] = deque(maxlen=window)  # True = observacao ok, False = erro
        self.declared: Optional[frozenset[str]] = None

    def set_declared(self, fields: Optional[frozenset[str]]) -> None:
        self.declared = fields

    def feed(self, state: GameState, latency_ms: Optional[float] = None) -> None:
        self.states.append(state)
        self.outcomes.append(True)
        if latency_ms is not None:
            self.latencies.append(latency_ms)

    def feed_error(self) -> None:
        self.outcomes.append(False)

    def _declared(self, fields: tuple[str, ...]) -> Optional[bool]:
        return None if self.declared is None else all(f in self.declared for f in fields)

    def report(self) -> list[Readiness]:
        n = len(self.states)
        out = []
        for req, fields in FIELDS.items():
            declared = self._declared(fields)
            if n < self.min_samples:
                out.append(Readiness(req, ReadinessStatus.INSUFFICIENT, f"{n}/{self.min_samples} amostras", declared))
                continue
            known = sum(all(getattr(s, f).known for f in fields) for s in self.states)
            ratio = known / n
            ok = ratio >= self.coverage and declared is not False
            detail = f"conhecido em {ratio:.0%} das ultimas {n} observacoes"
            if declared is False:
                detail += "; adaptador nao declara este campo"
            out.append(Readiness(req, ReadinessStatus.MET if ok else ReadinessStatus.NOT_MET, detail, declared))

        if n < self.min_samples:
            out.append(Readiness(Requirement.PROVENANCE, ReadinessStatus.INSUFFICIENT, f"{n}/{self.min_samples} amostras"))
        else:
            facts = [getattr(s, f) for s in self.states for f in GameState.FACT_FIELDS if getattr(s, f).known]
            without_source = sum(1 for f in facts if not f.source)
            with_conf = sum(1 for f in facts if f.confidence is not None)
            status = ReadinessStatus.MET if facts and not without_source else ReadinessStatus.NOT_MET
            detail = (f"{len(facts)} valores conhecidos; {without_source} sem fonte; "
                      f"{with_conf} com confianca" if facts else "nenhum valor conhecido")
            out.append(Readiness(Requirement.PROVENANCE, status, detail))

        total = len(self.outcomes)
        if total < self.min_samples:
            out.append(Readiness(Requirement.ADAPTER_HEALTH, ReadinessStatus.INSUFFICIENT,
                                 f"{total}/{self.min_samples} tentativas de observacao"))
        else:
            errors = self.outcomes.count(False) / total
            lat = sorted(self.latencies)
            p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None
            ok = errors <= self.max_error_rate and p95 is not None and p95 <= self.max_p95_latency_ms
            lat_text = "latencia nao medida" if p95 is None else f"p95 {p95:.1f} ms (limite {self.max_p95_latency_ms:g})"
            out.append(Readiness(Requirement.ADAPTER_HEALTH, ReadinessStatus.MET if ok else ReadinessStatus.NOT_MET,
                                 f"erros {errors:.0%} (limite {self.max_error_rate:.0%}); {lat_text}"))
        return out

    def ready(self) -> bool:
        return all(r.status is ReadinessStatus.MET for r in self.report())
