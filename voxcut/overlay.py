"""Screen tools used while recording other windows: draw over anything, laser pointer, spotlight, cursor highlight,
the floating recorder bar, a drag-to-select area picker, and global hotkeys.

The overlay is a transparent always-on-top window, so it is part of what the screen recorder captures.
The recorder bar is excluded from the capture (see recorder.exclude_from_capture).
"""
from __future__ import annotations

import math
import sys
import time
from collections import deque

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

from .drawing import DrawModel, Item, paint_item
from .recorder import IS_WIN, exclude_from_capture, list_monitors

DRAW_TOOLS = ("pen", "highlighter", "arrow", "rect", "eraser")
PASSIVE_TOOLS = ("none", "laser", "spotlight")
QUICK_COLORS = ["#ff3b30", "#2f6bff", "#16a34a", "#facc15"]


def _lbutton_down() -> bool:
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- drawing over the screen
class ScreenOverlay(QWidget):
    def __init__(self, screen):
        super().__init__()
        self._screen = screen
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setGeometry(screen.geometry())
        self.model = DrawModel()
        self.tool = "none"
        self.color = QUICK_COLORS[0]
        self.pen_w = 5.0
        self.cursor_ring = True
        self.cur: Item | None = None
        self.trail: deque = deque(maxlen=14)
        self._last = (None, False)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.set_tool("none")

    # ---- tools
    def set_tool(self, tool: str):
        self.tool = tool
        passive = tool in PASSIVE_TOOLS
        self.setWindowFlag(Qt.WindowTransparentForInput, passive)
        self.setCursor(Qt.ArrowCursor if passive else Qt.CrossCursor)
        self.cur = None
        self.show()
        self.raise_()
        if passive and tool == "none" and not self.cursor_ring:
            self._timer.stop()
        else:
            self._timer.start(16)
        self.update()

    def set_color(self, c: str):
        self.color = c

    def clear(self):
        self.model.clear()
        self.update()

    def undo(self):
        if self.model.undo():
            self.update()

    # ---- animation / cursor
    def _cursor_local(self) -> QPointF:
        return QPointF(self.mapFromGlobal(QCursor.pos()))

    def _tick(self):
        p = self._cursor_local()
        down = _lbutton_down()
        key = ((round(p.x()), round(p.y())), down)
        if self.tool == "laser":
            self.trail.append((p, time.monotonic()))
        elif self.trail:
            self.trail.clear()
        if key != self._last or (self.tool == "laser" and self.trail):
            self._last = key
            self.update()

    # ---- mouse (draw modes only; passive modes are click-through)
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        p = (e.position().x(), e.position().y())
        if self.tool == "eraser":
            self.model.erase_at(p, 14 + self.pen_w)
        elif self.tool in DRAW_TOOLS:
            pts = (p, p) if self.tool in ("arrow", "rect") else (p,)
            self.cur = Item(self.tool, pts, self.color, self.pen_w)
        self.update()

    def mouseMoveEvent(self, e):
        p = (e.position().x(), e.position().y())
        if self.tool == "eraser" and e.buttons() & Qt.LeftButton:
            self.model.erase_at(p, 14 + self.pen_w)
        elif self.cur is not None:
            c = self.cur
            if c.kind in ("pen", "highlighter"):
                if (p[0] - c.pts[-1][0]) ** 2 + (p[1] - c.pts[-1][1]) ** 2 >= 4:
                    self.cur = Item(c.kind, c.pts + (p,), c.color, c.width)
            else:
                self.cur = Item(c.kind, (c.pts[0], p), c.color, c.width)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.cur is not None:
            self.model.add(self.cur)
            self.cur = None
            self.update()

    # ---- paint
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # fully transparent pixels would let clicks fall through in draw mode; keep a nearly invisible tint
        if self.tool in DRAW_TOOLS:
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        for it in self.model.items:
            paint_item(p, it)
        if self.cur is not None:
            paint_item(p, self.cur)
        c = self._cursor_local()
        if self.tool == "spotlight":
            path = QPainterPath()
            path.addRect(QRectF(self.rect()))
            path.addEllipse(c, 170, 170)
            path.setFillRule(Qt.OddEvenFill)
            p.fillPath(path, QColor(0, 0, 0, 150))
        elif self.tool == "laser":
            now = time.monotonic()
            for pos, t in self.trail:
                a = max(0.0, 1.0 - (now - t) / 0.45)
                if a > 0:
                    col = QColor(255, 40, 40, int(150 * a))
                    p.setPen(Qt.NoPen)
                    p.setBrush(col)
                    p.drawEllipse(pos, 9 * a + 3, 9 * a + 3)
            p.setBrush(QColor(255, 60, 60, 230))
            p.setPen(QPen(QColor(255, 255, 255, 220), 2))
            p.drawEllipse(c, 9, 9)
        if self.cursor_ring and self.tool in ("none", "laser", "spotlight"):
            down = self._last[1]
            col = QColor(255, 140, 0, 200) if down else QColor(255, 220, 0, 110)
            p.setPen(QPen(col, 4 if down else 2))
            p.setBrush(QColor(255, 220, 0, 55) if not down else QColor(255, 140, 0, 70))
            r = 24 if down else 30
            if self.tool != "laser":
                p.drawEllipse(c, r, r)
        p.end()


# --------------------------------------------------------------------------- floating recorder bar
class RecorderBar(QWidget):
    pause_toggled = Signal()
    stop_clicked = Signal()
    marker_clicked = Signal()
    tool_selected = Signal(str)
    color_selected = Signal(str)
    undo_clicked = Signal()
    clear_clicked = Signal()
    board_clicked = Signal()

    STYLE = """
    #bar { background: rgba(24,26,44,235); border: 1px solid #3a3f66; border-radius: 12px; }
    QPushButton { background: #2a2e52; color: #e8eaff; border: 1px solid #3a3f66; border-radius: 7px;
                  padding: 5px 9px; font-size: 12px; }
    QPushButton:hover { background: #3a3f70; }
    QPushButton:checked { background: #5b6cff; border-color: #8f9bff; }
    QPushButton#stop { background: #c62828; border-color: #ef5350; }
    QLabel { color: #e8eaff; font-size: 13px; font-weight: 600; }
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(self.STYLE)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QWidget(objectName="bar")
        outer.addWidget(box)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(6)
        self.time_lbl = QLabel("● 00:00")
        lay.addWidget(self.time_lbl)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.pause_toggled)
        lay.addWidget(self.pause_btn)
        stop = QPushButton("Stop", objectName="stop")
        stop.clicked.connect(self.stop_clicked)
        lay.addWidget(stop)
        mk = QPushButton("Marker")
        mk.setToolTip("Add a chapter marker at this moment (Ctrl+Alt+M)")
        mk.clicked.connect(self.marker_clicked)
        lay.addWidget(mk)
        lay.addSpacing(6)
        self.tool_btns = {}
        for key, text, tip in (("none", "Mouse", "Use your computer normally"), ("pen", "Pen", "Draw on screen"),
                               ("highlighter", "Marker pen", "Highlight text"), ("arrow", "Arrow", ""),
                               ("rect", "Box", ""), ("laser", "Laser", "Laser pointer"),
                               ("spotlight", "Spotlight", "Dim everything except around the cursor"),
                               ("eraser", "Eraser", "Click a drawing to remove it")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.select_tool(k))
            lay.addWidget(b)
            self.tool_btns[key] = b
        self.tool_btns["none"].setChecked(True)
        for c in QUICK_COLORS:
            b = QPushButton("")
            b.setFixedSize(22, 22)
            b.setStyleSheet(f"QPushButton{{background:{c};border:2px solid #e8eaff;border-radius:11px;padding:0}}")
            b.clicked.connect(lambda _=False, col=c: self.color_selected.emit(col))
            lay.addWidget(b)
        for text, sig in (("Undo", self.undo_clicked), ("Clear", self.clear_clicked), ("Whiteboard", self.board_clicked)):
            b = QPushButton(text)
            b.clicked.connect(sig)
            lay.addWidget(b)
        self._drag = None
        self.adjustSize()

    def select_tool(self, key: str):
        for k, b in self.tool_btns.items():
            b.setChecked(k == key)
        self.tool_selected.emit(key)

    def set_time(self, text: str, paused: bool):
        self.time_lbl.setText(("❚❚ " if paused else "● ") + text)
        self.time_lbl.setStyleSheet("color:#ffb74d" if paused else "color:#ff6b6b")
        self.pause_btn.setText("Resume" if paused else "Pause")

    def place_on(self, screen):
        g = screen.geometry()
        self.adjustSize()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 14)

    # drag the bar by its background
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def showEvent(self, e):
        super().showEvent(e)
        exclude_from_capture(self)


# --------------------------------------------------------------------------- drag-to-select area
class _RegionWidget(QWidget):
    def __init__(self, owner, screen):
        super().__init__()
        self.owner, self.screen_ = owner, screen
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.a = self.b = None

    def rect_sel(self):
        return QRect(self.a, self.b).normalized() if self.a and self.b else None

    def mousePressEvent(self, e):
        self.a = self.b = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e):
        if self.a:
            self.b = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        r = self.rect_sel()
        if r and r.width() > 30 and r.height() > 30:
            self.owner._finish(self.screen_, r)
        else:
            self.a = self.b = None
            self.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.owner._cancel()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        r = self.rect_sel()
        if r:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor("#5b6cff"), 2))
            p.drawRect(r)
            p.setPen(QColor("white"))
            p.drawText(r.x() + 6, max(r.y() - 8, 16), f"{r.width()} x {r.height()}")
        else:
            p.setPen(QColor("white"))
            p.drawText(self.rect(), Qt.AlignCenter, "Drag to choose the area to record   -   Esc to cancel")


class RegionSelector(QObject):
    selected = Signal(tuple)      # (x, y, w, h) in physical pixels
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.ws = []

    def start(self):
        for s in QGuiApplication.screens():
            w = _RegionWidget(self, s)
            self.ws.append(w)
            w.showFullScreen()
            w.activateWindow()

    def _close(self):
        for w in self.ws:
            w.close()
        self.ws = []

    def _cancel(self):
        self._close()
        self.cancelled.emit()

    def _finish(self, screen, r: QRect):
        dpr = screen.devicePixelRatio()
        mons = {m["name"]: m for m in list_monitors()}
        m = mons.get(screen.name())
        ox, oy = (m["x"], m["y"]) if m else (screen.geometry().x(), screen.geometry().y())
        rect = (int(ox + r.x() * dpr), int(oy + r.y() * dpr), int(r.width() * dpr), int(r.height() * dpr))
        self._close()
        self.selected.emit(rect)


# --------------------------------------------------------------------------- global hotkeys (Windows)
class Hotkeys(QAbstractNativeEventFilter):
    MOD = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4}

    def __init__(self):
        super().__init__()
        self.cb = {}
        self.ids = []
        self.failed = []
        self._installed = False

    def register(self, hid: int, mods: str, key: str, callback) -> bool:
        if not IS_WIN:
            return False
        import ctypes
        m = sum(self.MOD[x] for x in mods.split("+")) | 0x4000          # MOD_NOREPEAT
        ok = bool(ctypes.windll.user32.RegisterHotKey(None, hid, m, ord(key.upper())))
        if ok:
            self.cb[hid] = callback
            self.ids.append(hid)
            if not self._installed:
                QApplication.instance().installNativeEventFilter(self)
                self._installed = True
        else:
            self.failed.append(f"{mods}+{key}")
        return ok

    def unregister_all(self):
        if IS_WIN:
            import ctypes
            for hid in self.ids:
                ctypes.windll.user32.UnregisterHotKey(None, hid)
        self.ids, self.cb = [], {}
        if self._installed:
            QApplication.instance().removeNativeEventFilter(self)
            self._installed = False

    def nativeEventFilter(self, event_type, message):
        if IS_WIN and event_type == b"windows_generic_MSG":
            try:
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312 and msg.wParam in self.cb:
                    QTimer.singleShot(0, self.cb[msg.wParam])
                    return True, 0
            except Exception:  # noqa: BLE001
                pass
        return False, 0
