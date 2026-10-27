"""Mapa esquematico XY: marcadores, trajetoria, destino pedido, zona e rota.
Sem geodata, textura ou mapa raster; o rotulo deixa isso explicito.
Cada elemento e uma camada que o operador liga/desliga (registro 022)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QWidget

from ..mapping import MapTransform
from ..model import GameState, Position
from ..routes import RouteGraph

MAX_TRAIL = 2000
LAYERS = (("grade", "Grade"), ("trilha", "Trilha"), ("entidades", "Entidades"), ("zona", "Zona"),
          ("rota", "Rota"), ("caminho", "Menor caminho"))


class MapView(QWidget):
    pointPicked = Signal(object)        # Position escolhida pelo operador (clique)
    zoneCenterRequested = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.transform = MapTransform()
        self.state: Optional[GameState] = None
        self.trail: list[Position] = []
        self.pending_target: Optional[Position] = None
        self.picked: Optional[Position] = None
        self.zone: Optional[tuple[Position, float]] = None
        self._drag: Optional[QPointF] = None
        self._press = QPointF()
        self._auto_fit = True
        self.hover_text = ""
        self.layers = {key: True for key, _ in LAYERS}
        self.route: Optional[RouteGraph] = None
        self.route_path: list[int] = []
        self.route_cursor: Optional[int] = None   # ultimo no adicionado no modo de edicao

    # --- dados ----------------------------------------------------------------
    def reset(self) -> None:
        self.state, self.trail, self.pending_target, self._auto_fit = None, [], None, True
        self.update()

    def set_zone(self, center: Optional[Position], radius: float) -> None:
        self.zone = (center, radius) if center else None
        self.update()

    def set_layer(self, key: str, visible: bool) -> None:
        if key not in self.layers:
            raise KeyError(key)
        self.layers[key] = visible
        self.update()

    def set_route(self, route: Optional[RouteGraph], path: Optional[list[int]] = None,
                  cursor: Optional[int] = None) -> None:
        self.route, self.route_path, self.route_cursor = route, list(path or []), cursor
        self.update()

    def set_pending_target(self, p: Optional[Position]) -> None:
        self.pending_target = p
        self.update()

    def update_state(self, state: GameState) -> None:
        self.state = state
        if state.self_position.known:
            p = state.self_position.value
            if not self.trail or self.trail[-1] != p:
                self.trail.append(p)
                del self.trail[:-MAX_TRAIL]
        if self._auto_fit:
            self.fit()
        self.update()

    def _points(self) -> list[Position]:
        pts = list(self.trail)
        if self.state:
            for fld in (self.state.characters, self.state.npcs):
                if fld.known:
                    pts += [e.position.value for e in fld.value if e.position.known]
        if self.zone:
            c, r = self.zone
            pts += [Position(c.x - r, c.y - r, c.z), Position(c.x + r, c.y + r, c.z)]
        if self.route:
            pts += list(self.route.nodes.values())
        return pts

    def fit(self) -> None:
        self.transform.resize(self.width(), self.height())
        self.transform.fit(self._points())
        self.update()

    # --- interacao --------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        self.transform.resize(self.width(), self.height())
        super().resizeEvent(event)

    def wheelEvent(self, event) -> None:
        self._auto_fit = False
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        pos = event.position()
        self.transform.zoom(factor, pos.x(), pos.y())
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.position()
            self._press = event.position()

    def mouseMoveEvent(self, event) -> None:
        wx, wy = self.transform.to_world(event.position().x(), event.position().y())
        self.hover_text = f"x={wx:.0f} y={wy:.0f}"
        if self._drag is not None:
            self._auto_fit = False
            d = event.position() - self._drag
            self.transform.pan(d.x(), d.y())
            self._drag = event.position()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            moved = (event.position() - self._press).manhattanLength()
            self._drag = None
            if moved < 4:
                self.pick(event.position().x(), event.position().y())

    def pick(self, vx: float, vy: float) -> Position:
        wx, wy = self.transform.to_world(vx, vy)
        z = self.state.self_position.value.z if self.state and self.state.self_position.known else 0
        self.picked = Position(round(wx), round(wy), z)
        self.pointPicked.emit(self.picked)
        self.update()
        return self.picked

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        fit = menu.addAction("Enquadrar tudo")
        center = menu.addAction("Usar ponto escolhido como centro da zona")
        center.setEnabled(self.picked is not None)
        chosen = menu.exec(event.globalPos())
        if chosen is fit:
            self._auto_fit = True
            self.fit()
        elif chosen is center and self.picked is not None:
            self.zoneCenterRequested.emit(self.picked)

    # --- desenho ------------------------------------------------------------------
    def _v(self, p: Position) -> QPointF:
        return QPointF(*self.transform.to_view(p.x, p.y))

    def paintEvent(self, event) -> None:
        t = self.transform
        t.resize(self.width(), self.height())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        text_color = self.palette().text().color()

        step = t.grid_step()
        x0, y0 = t.to_world(0, 0)
        x1, y1 = t.to_world(self.width(), self.height())
        if self.layers["grade"]:
            painter.setPen(QPen(QColor(128, 128, 128, 50)))
            gx = (x0 // step) * step
            while gx <= x1:
                vx = t.to_view(gx, 0)[0]
                painter.drawLine(QPointF(vx, 0), QPointF(vx, self.height()))
                gx += step
            gy = (y0 // step) * step
            while gy <= y1:
                vy = t.to_view(0, gy)[1]
                painter.drawLine(QPointF(0, vy), QPointF(self.width(), vy))
                gy += step

        if self.route and self.layers["rota"]:
            painter.setPen(QPen(QColor("#8d6e63"), 1.2))
            for edge in self.route.edges:
                a, b = tuple(edge)
                painter.drawLine(self._v(self.route.nodes[a]), self._v(self.route.nodes[b]))
            for nid, p in self.route.nodes.items():
                v = self._v(p)
                painter.setBrush(QColor("#ffb300" if nid == self.route_cursor else "#8d6e63"))
                painter.drawEllipse(v, 3.5, 3.5)
                painter.drawText(v + QPointF(5, 12), str(nid))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.route and self.layers["caminho"] and len(self.route_path) > 1:
            path = QPainterPath(self._v(self.route.nodes[self.route_path[0]]))
            for nid in self.route_path[1:]:
                path.lineTo(self._v(self.route.nodes[nid]))
            painter.setPen(QPen(QColor("#00897b"), 3))
            painter.drawPath(path)

        if self.zone and self.layers["zona"]:
            c, r = self.zone
            painter.setPen(QPen(QColor("#ef6c00"), 1.5, Qt.PenStyle.DashLine))
            painter.drawEllipse(self._v(c), r * t.scale, r * t.scale)

        if len(self.trail) > 1 and self.layers["trilha"]:
            path = QPainterPath(self._v(self.trail[0]))
            for p in self.trail[1:]:
                path.lineTo(self._v(p))
            painter.setPen(QPen(QColor("#42a5f5"), 1.5))
            painter.drawPath(path)

        st = self.state
        if st:
            for fld, color in ((st.npcs, "#e53935"), (st.characters, "#7e57c2")):
                if not fld.known or not self.layers["entidades"]:
                    continue
                for e in fld.value:
                    if not e.position.known:
                        continue
                    v = self._v(e.position.value)
                    painter.setPen(QPen(QColor(color), 1))
                    painter.setBrush(QColor(color))
                    painter.drawEllipse(v, 4, 4)
                    painter.setPen(text_color)
                    painter.drawText(v + QPointF(6, -6), e.name or (str(e.npc_id) if e.npc_id else "?"))
            if st.self_position.known:
                me = self._v(st.self_position.value)
                if self.pending_target:
                    painter.setPen(QPen(QColor("#2e7d32"), 1.2, Qt.PenStyle.DashLine))
                    painter.drawLine(me, self._v(self.pending_target))
                    painter.drawEllipse(self._v(self.pending_target), 3, 3)
                painter.setPen(QPen(QColor("#0d47a1"), 2))
                painter.setBrush(QColor("#1e88e5"))
                painter.drawEllipse(me, 6, 6)
        if self.picked:
            painter.setPen(QPen(QColor("#ef6c00"), 2))
            v = self._v(self.picked)
            painter.drawLine(v + QPointF(-6, 0), v + QPointF(6, 0))
            painter.drawLine(v + QPointF(0, -6), v + QPointF(0, 6))

        painter.setPen(text_color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        no_pos = st is None or not st.self_position.known
        caption = (f"Plano XY esquematico (sem geodata) · grade {step:g} u · {self.hover_text}"
                   + (" · posicao propria desconhecida" if no_pos else ""))
        painter.drawText(QRectF(6, self.height() - 22, self.width() - 12, 18), caption)
        painter.end()
