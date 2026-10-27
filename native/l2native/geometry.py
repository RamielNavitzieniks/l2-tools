"""Geometria pura usada pelos scripts (portada dos experimentos 015-017)."""
from __future__ import annotations

import math

from .model import Position


def distance_xy(a: Position, b: Position) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def follow_point(follower: Position, leader: Position, distance: float) -> Position:
    """Ponto a `distance` unidades antes da lider, na reta seguidora->lider
    (mesma formula do Teste017). Coordenadas arredondadas como no Pascal."""
    dx, dy = leader.x - follower.x, leader.y - follower.y
    gap = math.hypot(dx, dy)
    if gap == 0:
        return Position(round(follower.x), round(follower.y), round(leader.z))
    return Position(round(leader.x - distance * dx / gap), round(leader.y - distance * dy / gap), round(leader.z))


def circle_points(center: Position, radius: float, count: int) -> list[Position]:
    """Vertices de um poligono regular inscrito (Teste015/016), sentido anti-horario."""
    if count < 3 or radius <= 0:
        raise ValueError("circulo precisa de raio > 0 e ao menos 3 pontos")
    return [
        Position(round(center.x + radius * math.cos(2 * math.pi * i / count)),
                 round(center.y + radius * math.sin(2 * math.pi * i / count)),
                 center.z)
        for i in range(count)
    ]
