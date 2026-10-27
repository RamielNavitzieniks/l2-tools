"""Transformacao mundo <-> tela do mapa esquematico XY (sem Qt, testavel).

Convencao: X cresce para a direita e Y do jogo cresce para BAIXO na tela
(no Lineage II, Y cresce para o sul). E um plano sintetico: nao ha geodata,
textura nem mapa raster.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import Position


@dataclass
class MapTransform:
    center_x: float = 0.0
    center_y: float = 0.0
    scale: float = 0.2          # pixels por unidade do jogo
    width: float = 800.0
    height: float = 600.0
    min_scale: float = 0.001
    max_scale: float = 20.0

    def to_view(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.center_x) * self.scale + self.width / 2,
                (y - self.center_y) * self.scale + self.height / 2)

    def to_world(self, vx: float, vy: float) -> tuple[float, float]:
        return ((vx - self.width / 2) / self.scale + self.center_x,
                (vy - self.height / 2) / self.scale + self.center_y)

    def resize(self, width: float, height: float) -> None:
        self.width, self.height = max(1.0, width), max(1.0, height)

    def zoom(self, factor: float, anchor_vx: float, anchor_vy: float) -> None:
        """Zoom mantendo fixo o ponto do mundo sob o cursor."""
        wx, wy = self.to_world(anchor_vx, anchor_vy)
        self.scale = min(self.max_scale, max(self.min_scale, self.scale * factor))
        self.center_x = wx - (anchor_vx - self.width / 2) / self.scale
        self.center_y = wy - (anchor_vy - self.height / 2) / self.scale

    def pan(self, dvx: float, dvy: float) -> None:
        self.center_x -= dvx / self.scale
        self.center_y -= dvy / self.scale

    def fit(self, points: Iterable[Position], margin: float = 0.15, min_span: float = 600.0) -> bool:
        pts = list(points)
        if not pts:
            return False
        xs, ys = [p.x for p in pts], [p.y for p in pts]
        span_x, span_y = max(max(xs) - min(xs), min_span), max(max(ys) - min(ys), min_span)
        self.center_x, self.center_y = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        usable = 1 - 2 * margin
        self.scale = min(self.max_scale, max(self.min_scale,
                                             min(self.width * usable / span_x, self.height * usable / span_y)))
        return True

    def grid_step(self, target_px: float = 80.0) -> float:
        """Passo de grade 'redondo' (1, 2, 5 x 10^n) proximo de target_px na tela."""
        raw = target_px / self.scale
        magnitude = 10 ** int(f"{raw:e}".split("e")[1])
        for m in (1, 2, 5, 10):
            if m * magnitude >= raw:
                return m * magnitude
        return 10 * magnitude
