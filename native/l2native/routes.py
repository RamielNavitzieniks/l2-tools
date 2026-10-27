"""Grafo de rota proprio (formato l2tools-route/1) e menor caminho.

Rotas sao desenhadas pelo operador no mapa XY ou escritas a mao. O campo
`provenance` e obrigatorio: registra de onde vieram os pontos (registro 022:
apenas dados proprios ou licenciados; nunca GPS/mapas de terceiros).
"""
from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .model import Position

FORMAT = "l2tools-route/1"


def _dist(a: Position, b: Position) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


@dataclass
class RouteGraph:
    provenance: str = "desenhada no L2-Tools (dados proprios)"
    nodes: dict[int, Position] = field(default_factory=dict)
    edges: set[frozenset[int]] = field(default_factory=set)
    _next: int = 1

    def add_node(self, p: Position, connect_to: Optional[int] = None) -> int:
        nid = self._next
        self._next += 1
        self.nodes[nid] = p
        if connect_to is not None:
            self.connect(nid, connect_to)
        return nid

    def connect(self, a: int, b: int) -> None:
        if a == b or a not in self.nodes or b not in self.nodes:
            raise ValueError(f"aresta invalida: {a}-{b}")
        self.edges.add(frozenset((a, b)))

    def remove_node(self, nid: int) -> None:
        del self.nodes[nid]
        self.edges = {e for e in self.edges if nid not in e}

    def neighbors(self, nid: int) -> list[int]:
        return [next(iter(e - {nid})) for e in self.edges if nid in e]

    def nearest(self, p: Position) -> Optional[int]:
        if not self.nodes:
            return None
        return min(self.nodes, key=lambda n: _dist(self.nodes[n], p))

    def shortest_path(self, start: int, goal: int) -> Optional[list[int]]:
        """Dijkstra por distancia XYZ. None se nao houver ligacao."""
        if start not in self.nodes or goal not in self.nodes:
            raise KeyError("no inexistente")
        best = {start: 0.0}
        previous: dict[int, int] = {}
        queue = [(0.0, start)]
        while queue:
            cost, nid = heapq.heappop(queue)
            if nid == goal:
                path = [goal]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return path[::-1]
            if cost > best[nid]:
                continue
            for nb in self.neighbors(nid):
                c = cost + _dist(self.nodes[nid], self.nodes[nb])
                if c < best.get(nb, math.inf):
                    best[nb], previous[nb] = c, nid
                    heapq.heappush(queue, (c, nb))
        return None

    def length(self, path: list[int]) -> float:
        return sum(_dist(self.nodes[a], self.nodes[b]) for a, b in zip(path, path[1:]))

    def to_dict(self) -> dict[str, Any]:
        return {"format": FORMAT, "provenance": self.provenance,
                "nodes": [{"id": n, "x": p.x, "y": p.y, "z": p.z} for n, p in sorted(self.nodes.items())],
                "edges": sorted(sorted(e) for e in self.edges)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouteGraph":
        if data.get("format") != FORMAT:
            raise ValueError(f"formato de rota nao reconhecido (esperado {FORMAT})")
        provenance = str(data.get("provenance", "")).strip()
        if not provenance:
            raise ValueError("rota sem 'provenance': informe a origem dos pontos")
        g = cls(provenance)
        for n in data.get("nodes", []):
            nid = int(n["id"])
            if nid in g.nodes:
                raise ValueError(f"no repetido: {nid}")
            g.nodes[nid] = Position(n["x"], n["y"], n["z"])
        g._next = max(g.nodes, default=0) + 1
        for a, b in data.get("edges", []):
            g.connect(int(a), int(b))
        return g

    def save(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> "RouteGraph":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
