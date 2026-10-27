"""VisualAdapter: observa o cliente por captura de imagem. Nao executa acoes.

Produz apenas `quest_dialog` (observado, com a confianca do casamento visual);
todos os outros campos ficam desconhecidos. Falha de captura vira
`quest_dialog` desconhecido com o motivo, nunca "ausente".
"""
from __future__ import annotations

import time
from typing import Callable

from ..actions import ActionKind, ActionResult, Intent, ResultStatus
from ..model import GameState, observed, unknown
from ..vision.capture import Capture, CaptureExhausted, CaptureUnavailable
from ..vision.detector import Detection, DetectionStatus, TemplateDetector
from .base import SourceExhausted


class VisualAdapter:
    name = "visual"

    def __init__(self, capture: Capture, detector: TemplateDetector, *, samples: int = 1,
                 sample_interval: float = 0.25, max_shift_px: int = 12,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if samples < 1:
            raise ValueError("samples >= 1")
        self._capture = capture
        self._detector = detector
        self._samples = samples
        self._interval = sample_interval
        self._max_shift = max_shift_px
        self._sleep = sleep
        self._tick = 0
        self.last_detection: Detection | None = None

    def _detect(self) -> Detection:
        found: list[Detection] = []
        for i in range(self._samples):
            if i:
                self._sleep(self._interval)
            try:
                frame = self._capture.grab()
            except CaptureUnavailable as exc:
                return Detection(DetectionStatus.UNAVAILABLE, reason=str(exc))
            det = self._detector.detect(frame)
            if det.status is DetectionStatus.UNAVAILABLE:
                return det
            found.append(det)
        weakest = min(found, key=lambda d: d.confidence or 0.0)
        if all(d.status is DetectionStatus.DETECTED for d in found):
            stable = all(abs(d.x - found[0].x) <= self._max_shift and abs(d.y - found[0].y) <= self._max_shift
                         for d in found)
            if stable:
                return weakest
            return Detection(DetectionStatus.NOT_DETECTED, weakest.confidence, reason="POSICAO_INSTAVEL")
        return Detection(DetectionStatus.NOT_DETECTED, weakest.confidence, reason="")

    def observe(self) -> GameState:
        try:
            det = self._detect()
        except CaptureExhausted as exc:
            raise SourceExhausted(str(exc)) from exc
        self._tick += 1
        self.last_detection = det
        src = "visual:template"
        if det.status is DetectionStatus.UNAVAILABLE:
            dialog = unknown(src, det.reason)
        else:
            note = "" if det.x is None else f"x={det.x} y={det.y} rel=({det.rel_x},{det.rel_y})"
            dialog = observed(det.status is DetectionStatus.DETECTED, src, det.confidence, note or det.reason)
        return GameState(tick=self._tick, quest_dialog=dialog)

    def observes(self) -> frozenset[str]:
        return frozenset({"quest_dialog"})

    def capabilities(self) -> frozenset[ActionKind]:
        return frozenset()

    def execute(self, intent: Intent) -> ActionResult:
        return ActionResult(intent, ResultStatus.UNSUPPORTED, "VisualAdapter apenas observa; cliques nao implementados")

    def close(self) -> None:
        self._capture.close()
