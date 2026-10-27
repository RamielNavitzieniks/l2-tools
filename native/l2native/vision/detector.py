"""Deteccao de janela de dialogo por imagem-modelo (OpenCV TM_CCOEFF_NORMED).

Evolucao de `scripts/python/dialogo_detector.py` (experimento 018, PR #12):
mesmas guardas (modelo pequeno/sem textura, captura preta, modelo maior que a
imagem) e mesmo limiar padrao 0.91, agora como biblioteca: sem arquivo de
resposta, sem processo externo, com varias escalas e posicao normalizada.

Requer `numpy` e `opencv-python-headless` (extra `vision`). Nada e gravado em disco.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Sequence


class VisionUnavailable(RuntimeError):
    """Dependencias de visao (numpy/OpenCV) ausentes."""


def _cv():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise VisionUnavailable("instale o extra de visao: pip install numpy opencv-python-headless") from exc
    return cv2, np


class DetectionStatus(str, Enum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    UNAVAILABLE = "unavailable"   # nao foi possivel capturar/avaliar: estado desconhecido


@dataclass(frozen=True)
class Detection:
    status: DetectionStatus
    confidence: Optional[float] = None
    x: Optional[int] = None            # canto superior esquerdo, pixels da regiao capturada
    y: Optional[int] = None
    rel_x: Optional[float] = None      # mesma posicao normalizada 0..1 (independe da resolucao)
    rel_y: Optional[float] = None
    scale: Optional[float] = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: (v.value if isinstance(v, Enum) else v)
                for k, v in self.__dict__.items() if v not in (None, "")}


def _gray(image: Any) -> Any:
    cv2, np = _cv()
    arr = np.asarray(image)
    if arr.ndim == 2:
        return arr.astype(np.uint8, copy=False)
    if arr.ndim == 3 and arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2GRAY)
    if arr.ndim == 3 and arr.shape[2] == 3:
        return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    raise ValueError("formato de imagem nao suportado")


class TemplateDetector:
    def __init__(self, template: Any, threshold: float = 0.91, scales: Sequence[float] = (1.0,)) -> None:
        _, np = _cv()
        self.template = _gray(template)
        if min(self.template.shape) < 12:
            raise ValueError("MODELO_PEQUENO: minimo 12x12 px")
        if float(np.std(self.template)) < 8:
            raise ValueError("MODELO_SEM_DETALHES: modelo sem textura suficiente")
        if not 0 < threshold <= 1:
            raise ValueError("limiar deve estar em (0, 1]")
        if not scales or any(s <= 0 for s in scales):
            raise ValueError("escalas devem ser positivas")
        self.threshold = threshold
        self.scales = tuple(scales)

    @classmethod
    def from_file(cls, path: str, **kwargs: Any) -> "TemplateDetector":
        cv2, _ = _cv()
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError("MODELO_AUSENTE: nao foi possivel ler o arquivo do modelo")
        return cls(image, **kwargs)

    def detect(self, frame: Any) -> Detection:
        cv2, np = _cv()
        if frame is None or np.asarray(frame).size == 0:
            return Detection(DetectionStatus.UNAVAILABLE, reason="IMAGEM_VAZIA")
        gray = _gray(frame)
        if float(np.mean(gray)) < 4.0 and float(np.std(gray)) < 3.0:
            return Detection(DetectionStatus.UNAVAILABLE, reason="CAPTURA_PRETA")
        best: Optional[tuple[float, int, int, float]] = None
        for scale in self.scales:
            tpl = self.template if scale == 1.0 else cv2.resize(
                self.template, None, fx=scale, fy=scale,
                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
            if tpl.shape[0] > gray.shape[0] or tpl.shape[1] > gray.shape[1] or min(tpl.shape) < 12:
                continue
            _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED))
            if best is None or score > best[0]:
                best = (float(score), int(loc[0]), int(loc[1]), scale)
        if best is None:
            return Detection(DetectionStatus.UNAVAILABLE, reason="MODELO_MAIOR_QUE_JANELA")
        score, x, y, scale = best
        status = DetectionStatus.DETECTED if score >= self.threshold else DetectionStatus.NOT_DETECTED
        h, w = gray.shape
        return Detection(status, round(max(0.0, min(1.0, score)), 4), x, y,
                         round(x / w, 4), round(y / h, 4), scale)
