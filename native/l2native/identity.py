"""Identidade observada da personagem, por leitura visual (experimento 028).

So OBSERVA: captura a regiao configurada da janela escolhida pelo operador,
le o nome por OCR local e compara com a associacao manual. Nada e enviado ao
jogo, nenhuma acao depende disto hoje e nenhum script e iniciado.

Duas etapas separadas:

1. `VisualIdentityProbe.probe(...)` -> `IdentityReading`: o que foi LIDO,
   com origem, confianca, instante e regiao. Um nome so e aceito se todas as
   amostras (padrao: 3) lerem o mesmo nome valido, cada uma acima do limiar,
   e sem outro candidato plausivel na mesma imagem.
2. `IdentityMatcher.compare(leitura, associacao)` -> `IdentityObservation`:
   compativel / divergente / observada sem associacao / desconhecida /
   indisponivel. Recalculada sempre que a associacao muda; leitura velha vira
   desconhecida ("nova leitura necessaria").

Comparacao conservadora: igualdade exata apos normalizacao Unicode (NFC) e sem
diferenciar maiusculas. Nome parecido nao confirma; fica desconhecido.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from .ocr import NameRules, OcrBackend, OcrUnavailable, name_key, normalize_text
from .vision.capture import CaptureExhausted, CaptureUnavailable
from .vision.frames import FrameSource, RegionOfInterest, blank_reason

SOURCE = "visual:ocr"


class ReadingStatus(str, Enum):
    READ = "lida"                    # nome lido de forma consistente
    INCONCLUSIVE = "inconclusiva"    # houve imagem, mas nao ha evidencia suficiente
    UNAVAILABLE = "indisponivel"     # captura, regiao ou OCR indisponivel


@dataclass(frozen=True)
class IdentityReading:
    session_id: str
    status: ReadingStatus
    observed_at: float
    reason: str = ""
    name: Optional[str] = None
    confidence: Optional[float] = None      # menor confianca entre as amostras
    samples: int = 0
    region: Optional[RegionOfInterest] = None
    source: str = SOURCE
    engine: str = ""

    def __post_init__(self) -> None:
        if (self.status is ReadingStatus.READ) != (self.name is not None):
            raise ValueError("so uma leitura 'lida' carrega nome")


class IdentityStatus(str, Enum):
    MATCH = "compativel"
    MISMATCH = "divergente"
    UNBOUND = "observada, sem associacao"
    UNKNOWN = "desconhecida"
    UNAVAILABLE = "indisponivel"


@dataclass(frozen=True)
class IdentityObservation:
    session_id: str
    status: IdentityStatus
    reason: str
    name: Optional[str] = None
    expected: Optional[str] = None
    confidence: Optional[float] = None
    observed_at: Optional[float] = None
    region: Optional[RegionOfInterest] = None
    source: str = SOURCE

    @property
    def blocks_actions(self) -> bool:
        """So uma identidade compativel e recente libera acoes que dependam dela."""
        return self.status is not IdentityStatus.MATCH

    def describe(self) -> str:
        texts = {
            IdentityStatus.MATCH: f"compativel: lido '{self.name}' = perfil associado",
            IdentityStatus.MISMATCH: f"DIVERGENTE: lido '{self.name}', associado '{self.expected}'",
            IdentityStatus.UNBOUND: f"observada '{self.name}' (sem associacao para comparar)",
            IdentityStatus.UNKNOWN: f"desconhecida: {self.reason}",
            IdentityStatus.UNAVAILABLE: f"indisponivel: {self.reason}",
        }
        return texts[self.status]


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


NOT_IDENTIFIED = "personagem ainda nao identificada (use 'Identificar personagem')"
STALE = "identificacao antiga; nova leitura necessaria"


class IdentityMatcher:
    def __init__(self, max_age_s: float = 120.0, similar_distance: int = 2) -> None:
        if max_age_s <= 0:
            raise ValueError("max_age_s deve ser positivo")
        self.max_age_s = max_age_s
        self.similar_distance = similar_distance

    def compare(self, session_id: str, reading: Optional[IdentityReading], expected: Optional[str],
                now: float) -> IdentityObservation:
        if reading is None:
            return IdentityObservation(session_id, IdentityStatus.UNKNOWN, NOT_IDENTIFIED, expected=expected)
        base = dict(expected=expected, confidence=reading.confidence, observed_at=reading.observed_at,
                    region=reading.region, source=reading.source)
        if reading.session_id != session_id:
            return IdentityObservation(session_id, IdentityStatus.UNKNOWN, "leitura de outra sessao descartada",
                                       expected=expected)
        if reading.status is ReadingStatus.UNAVAILABLE:
            return IdentityObservation(session_id, IdentityStatus.UNAVAILABLE, reading.reason, **base)
        if reading.status is ReadingStatus.INCONCLUSIVE:
            return IdentityObservation(session_id, IdentityStatus.UNKNOWN, f"OCR inconclusivo: {reading.reason}",
                                       **base)
        if now - reading.observed_at > self.max_age_s:
            return IdentityObservation(session_id, IdentityStatus.UNKNOWN, STALE, **base)
        name = reading.name
        if not expected:
            return IdentityObservation(session_id, IdentityStatus.UNBOUND, "sem associacao manual", name=name, **base)
        a, b = name_key(name), name_key(expected)
        if a == b:
            return IdentityObservation(session_id, IdentityStatus.MATCH, "nome lido igual ao associado", name=name,
                                       **base)
        if _distance(a, b) <= self.similar_distance:
            return IdentityObservation(session_id, IdentityStatus.UNKNOWN,
                                       f"nome lido parecido, mas nao igual ao associado ('{name}'): leia de novo "
                                       "ou confira a associacao", name=name, **base)
        return IdentityObservation(session_id, IdentityStatus.MISMATCH, "nome lido diferente do associado",
                                   name=name, **base)


@dataclass(frozen=True)
class _Sample:
    name: Optional[str]
    confidence: Optional[float]
    problem: str = ""


class VisualIdentityProbe:
    """Le o nome da propria personagem na regiao configurada de UMA janela.

    Nao enumera nem escolhe janelas: recebe a fonte de frames da janela
    selecionada pelo operador. Limiar e numero de amostras sao configuraveis;
    os valores padrao foram escolhidos pelos testes com imagens sinteticas, nao
    sao uma precisao medida no jogo."""

    def __init__(self, ocr: OcrBackend, *, samples: int = 3, interval_s: float = 0.4,
                 min_confidence: float = 0.80, name_rules: NameRules = NameRules(),
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> None:
        if samples < 1:
            raise ValueError("samples >= 1")
        if not 0 < min_confidence <= 1:
            raise ValueError("min_confidence deve estar em (0, 1]")
        self.ocr = ocr
        self.samples = samples
        self.interval_s = interval_s
        self.min_confidence = min_confidence
        self.name_rules = name_rules
        self._sleep = sleep
        self._clock = clock

    def _sample(self, image: Any) -> _Sample:
        result = self.ocr.recognize(image)
        words = [w for w in result.words if normalize_text(w.text)]
        if not words:
            return _Sample(None, None, "nenhum texto reconhecido")
        valid = [w for w in words if self.name_rules.valid(normalize_text(w.text))]
        confident = [w for w in valid if w.confidence is not None and w.confidence >= self.min_confidence]
        if len(confident) > 1:
            return _Sample(None, None, f"{len(confident)} candidatos plausiveis na regiao")
        if not confident:
            if valid and all(w.confidence is None for w in valid):
                return _Sample(None, None, "o OCR nao informou confianca")
            if valid:
                best = max(w.confidence or 0.0 for w in valid)
                return _Sample(None, best, f"confianca {best:.2f} abaixo do limiar {self.min_confidence:.2f}")
            return _Sample(None, None, "texto lido nao tem formato de nome")
        if len(valid) > 1:
            return _Sample(None, None, "outro texto com formato de nome na regiao (ambiguo)")
        return _Sample(normalize_text(confident[0].text), confident[0].confidence)

    def probe(self, session_id: str, frames: FrameSource, region: Optional[RegionOfInterest],
              cancel: Optional[threading.Event] = None) -> IdentityReading:
        def done(status: ReadingStatus, reason: str, n: int, name=None, conf=None, engine="") -> IdentityReading:
            return IdentityReading(session_id, status, self._clock(), reason, name, conf, n, region, SOURCE, engine)

        if region is None:
            return done(ReadingStatus.UNAVAILABLE, "REGIAO_NAO_CONFIGURADA: configure a regiao do nome", 0)
        taken: list[_Sample] = []
        engine = getattr(self.ocr, "name", "")
        for i in range(self.samples):
            if cancel is not None and cancel.is_set():
                return done(ReadingStatus.UNAVAILABLE, "CANCELADA pela operadora", len(taken))
            if i:
                self._sleep(self.interval_s)
            try:
                frame = frames.grab_frame(region)
            except (CaptureUnavailable, CaptureExhausted) as exc:
                return done(ReadingStatus.UNAVAILABLE, f"captura: {exc}", len(taken))
            problem = region.layout_problem(frame.client_size)
            if problem:
                return done(ReadingStatus.UNAVAILABLE, problem, len(taken))
            blank = blank_reason(frame.image)
            if blank:
                return done(ReadingStatus.UNAVAILABLE, blank, len(taken))
            try:
                sample = self._sample(frame.image)
            except OcrUnavailable as exc:
                return done(ReadingStatus.UNAVAILABLE, str(exc), len(taken))
            taken.append(sample)
            if sample.problem:
                return done(ReadingStatus.INCONCLUSIVE, f"amostra {i + 1}: {sample.problem}", len(taken),
                            engine=engine)
        keys = {name_key(s.name) for s in taken}
        if len(keys) > 1:
            return done(ReadingStatus.INCONCLUSIVE, f"amostras leram nomes diferentes ({len(keys)})", len(taken),
                        engine=engine)
        conf = min(s.confidence for s in taken)
        return done(ReadingStatus.READ, f"{len(taken)} amostra(s) concordantes", len(taken), taken[0].name, conf,
                    engine)
