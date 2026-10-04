"""Vector drawing model shared by the whiteboard and the on-screen drawing overlay.

Items are immutable once added, so undo/redo can simply keep snapshots of the item list.
Qt is only needed by paint_item(); the model itself is plain Python (easy to test).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple

Pt = Tuple[float, float]
TOOLS = ("pen", "highlighter", "line", "arrow", "rect", "ellipse", "text", "eraser")
HISTORY_LIMIT = 200


@dataclass(frozen=True)
class Item:
    kind: str                 # pen | highlighter | line | arrow | rect | ellipse | text
    pts: Tuple[Pt, ...]       # pen/highlighter: many points; shapes/line/arrow: 2 points; text: 1 point
    color: str = "#111111"
    width: float = 4.0
    text: str = ""


class DrawModel:
    def __init__(self):
        self.items: List[Item] = []
        self._undo: List[List[Item]] = []
        self._redo: List[List[Item]] = []

    # ---- history
    def _snapshot(self):
        self._undo.append(list(self.items))
        if len(self._undo) > HISTORY_LIMIT:
            self._undo.pop(0)
        self._redo.clear()

    def add(self, item: Item):
        self._snapshot()
        self.items.append(item)

    def clear(self):
        if self.items:
            self._snapshot()
            self.items = []

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(list(self.items))
        self.items = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(list(self.items))
        self.items = self._redo.pop()
        return True

    # ---- eraser (removes whole strokes/shapes under the cursor)
    def erase_at(self, p: Pt, radius: float = 14.0) -> bool:
        keep = [it for it in self.items if not hit(it, p, radius)]
        if len(keep) == len(self.items):
            return False
        self._snapshot()
        self.items = keep
        return True


# --------------------------------------------------------------------------- geometry
def _seg_dist(p: Pt, a: Pt, b: Pt) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def text_rect(it: Item) -> Tuple[float, float, float, float]:
    size = it.width * 4 + 10
    x, y = it.pts[0]
    lines = it.text.split("\n") or [""]
    return x, y, max(len(l) for l in lines) * size * 0.6 + 6, len(lines) * size * 1.35


def hit(it: Item, p: Pt, radius: float) -> bool:
    r = radius + it.width / 2
    if it.kind in ("pen", "highlighter"):
        pts = it.pts
        if len(pts) == 1:
            return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]) <= r
        return any(_seg_dist(p, pts[i], pts[i + 1]) <= r for i in range(len(pts) - 1))
    if it.kind in ("line", "arrow"):
        return _seg_dist(p, it.pts[0], it.pts[1]) <= r
    if it.kind == "rect":
        (x1, y1), (x2, y2) = it.pts
        l, rr, t, b = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
        c = [(l, t), (rr, t), (rr, b), (l, b), (l, t)]
        return any(_seg_dist(p, c[i], c[i + 1]) <= r for i in range(4))
    if it.kind == "ellipse":
        (x1, y1), (x2, y2) = it.pts
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        a, b = abs(x2 - x1) / 2 or 1, abs(y2 - y1) / 2 or 1
        d = math.hypot((p[0] - cx) / a, (p[1] - cy) / b)
        return abs(d - 1) * min(a, b) <= r
    if it.kind == "text":
        x, y, w, h = text_rect(it)
        return x - radius <= p[0] <= x + w + radius and y - radius <= p[1] <= y + h + radius
    return False


# --------------------------------------------------------------------------- painting (Qt)
def paint_item(painter, it: Item):
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPainterPath, QPen, QPolygonF

    col = QColor(it.color)
    if it.kind == "highlighter":
        col.setAlpha(95)
    pen = QPen(col, it.width * (3.2 if it.kind == "highlighter" else 1.0))
    pen.setCapStyle(Qt.FlatCap if it.kind == "highlighter" else Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    pts = [QPointF(x, y) for x, y in it.pts]
    if it.kind in ("pen", "highlighter"):
        if len(pts) == 1:
            painter.drawPoint(pts[0])
            return
        path = QPainterPath(pts[0])
        for i in range(1, len(pts) - 1):
            mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2, (pts[i].y() + pts[i + 1].y()) / 2)
            path.quadTo(pts[i], mid)
        path.lineTo(pts[-1])
        painter.drawPath(path)
    elif it.kind == "line":
        painter.drawLine(pts[0], pts[1])
    elif it.kind == "arrow":
        a, b = pts[0], pts[1]
        painter.drawLine(a, b)
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        size = max(12.0, it.width * 3.5)
        h1 = QPointF(b.x() - size * math.cos(ang - 0.45), b.y() - size * math.sin(ang - 0.45))
        h2 = QPointF(b.x() - size * math.cos(ang + 0.45), b.y() - size * math.sin(ang + 0.45))
        painter.setBrush(col)
        painter.drawPolygon(QPolygonF([b, h1, h2]))
    elif it.kind == "rect":
        painter.drawRect(QRectF(pts[0], pts[1]).normalized())
    elif it.kind == "ellipse":
        painter.drawEllipse(QRectF(pts[0], pts[1]).normalized())
    elif it.kind == "text":
        f = QFont("Segoe UI")
        f.setPixelSize(int(it.width * 4 + 10))
        painter.setFont(f)
        painter.setPen(col)
        x, y, w, h = text_rect(it)
        painter.drawText(QRectF(x, y, w + 400, h + 20), int(Qt.AlignLeft | Qt.AlignTop), it.text)
