"""Lecture whiteboard: multi-page board with pen, highlighter, shapes, text, eraser, undo/redo, PDF/PNG export.

Record it by choosing "Whiteboard window" as the source in the Lecture recorder (or put it fullscreen with F11
on the screen being recorded).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QColorDialog, QComboBox, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QSlider, QToolBar, QWidget)

from .drawing import DrawModel, Item, paint_item

BACKGROUNDS = {
    "White": "#ffffff", "Cream": "#fbf6e9", "Chalkboard": "#1f3b2d", "Black": "#101010",
    "Grid (white)": "#ffffff", "Lines (white)": "#ffffff", "Graph (white)": "#ffffff",
}
PALETTE = ["#111111", "#d62828", "#1d4ed8", "#15803d", "#f59e0b", "#7c3aed", "#ffffff", "#0ea5e9"]
TOOL_KEYS = {"pen": "P", "highlighter": "H", "eraser": "E", "line": "L", "arrow": "A", "rect": "R",
             "ellipse": "O", "text": "T"}


def _grid_color(bg: QColor) -> QColor:
    return QColor(0, 0, 0, 45) if bg.lightness() > 128 else QColor(255, 255, 255, 45)


class BoardCanvas(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.pages = [DrawModel()]
        self.page = 0
        self.tool = "pen"
        self.color = PALETTE[0]
        self.pen_w = 4.0
        self.bg_name = "White"
        self.cur: Item | None = None
        self.cursor_pt: QPointF | None = None
        self.text_provider = self._ask_text      # replaced in tests
        self.setMouseTracking(True)
        self.setMinimumSize(640, 400)
        self.setCursor(Qt.CrossCursor)

    # ---- model helpers
    @property
    def model(self) -> DrawModel:
        return self.pages[self.page]

    def set_tool(self, t):
        self.tool = t
        self.update()

    def new_page(self):
        self.pages.insert(self.page + 1, DrawModel())
        self.page += 1
        self.update(); self.changed.emit()

    def go(self, delta):
        n = min(max(self.page + delta, 0), len(self.pages) - 1)
        if n != self.page:
            self.page = n
            self.update(); self.changed.emit()

    def undo(self):
        if self.model.undo():
            self.update()

    def redo(self):
        if self.model.redo():
            self.update()

    def clear_page(self):
        self.model.clear()
        self.update()

    def _ask_text(self, pos):
        text, ok = QInputDialog.getMultiLineText(self, "Text", "Type your text:")
        return text if ok else ""

    # ---- input
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        p = (e.position().x(), e.position().y())
        if self.tool == "eraser":
            self.model.erase_at(p, 14 + self.pen_w)
            self.update()
            return
        if self.tool == "text":
            txt = self.text_provider(p)
            if txt and txt.strip():
                self.model.add(Item("text", (p,), self.color, self.pen_w, txt))
                self.update()
            return
        pts = (p, p) if self.tool in ("line", "arrow", "rect", "ellipse") else (p,)
        self.cur = Item(self.tool, pts, self.color, self.pen_w)
        self.update()

    def mouseMoveEvent(self, e):
        self.cursor_pt = e.position()
        p = (e.position().x(), e.position().y())
        if self.tool == "eraser" and e.buttons() & Qt.LeftButton:
            if self.model.erase_at(p, 14 + self.pen_w):
                pass
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

    def leaveEvent(self, _):
        self.cursor_pt = None
        self.update()

    # ---- painting
    def paint_page(self, painter: QPainter, size: QSize, model: DrawModel | None = None, with_current=False):
        bg = QColor(BACKGROUNDS[self.bg_name])
        painter.fillRect(0, 0, size.width(), size.height(), bg)
        gc = _grid_color(bg)
        painter.setPen(QPen(gc, 1))
        w, h = size.width(), size.height()
        if "Grid" in self.bg_name or "Graph" in self.bg_name:
            step = 40 if "Grid" in self.bg_name else 20
            for x in range(0, w, step):
                painter.drawLine(x, 0, x, h)
            for y in range(0, h, step):
                painter.drawLine(0, y, w, y)
        elif "Lines" in self.bg_name:
            for y in range(60, h, 44):
                painter.drawLine(0, y, w, y)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for it in (model or self.model).items:
            paint_item(painter, it)
        if with_current and self.cur is not None:
            paint_item(painter, self.cur)

    def paintEvent(self, _):
        p = QPainter(self)
        self.paint_page(p, self.size(), with_current=True)
        if self.tool == "eraser" and self.cursor_pt is not None:
            p.setPen(QPen(QColor(120, 120, 120), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            r = 14 + self.pen_w
            p.drawEllipse(self.cursor_pt, r, r)
        p.end()

    # ---- export
    def render_image(self, page: int | None = None, scale: float = 1.0) -> QImage:
        size = QSize(int(self.width_px() * scale), int(self.height_px() * scale))
        img = QImage(size, QImage.Format_ARGB32)
        p = QPainter(img)
        p.scale(scale, scale)
        self.paint_page(p, QSize(self.width_px(), self.height_px()), self.pages[self.page if page is None else page])
        p.end()
        return img

    def width_px(self):
        return max(self.width(), 640)

    def height_px(self):
        return max(self.height(), 400)

    def export_pdf(self, path: str) -> int:
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter
        w, h = self.width_px(), self.height_px()
        pdf = QPdfWriter(path)
        pdf.setResolution(96)
        pdf.setPageSize(QPageSize(QSizeF(w, h) * 25.4 / 96, QPageSize.Millimeter))
        pdf.setPageMargins(QMarginsF(0, 0, 0, 0))
        p = QPainter(pdf)
        for i, m in enumerate(self.pages):
            if i:
                pdf.newPage()
            self.paint_page(p, QSize(w, h), m)
        p.end()
        return len(self.pages)


class WhiteboardWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ideawood Whiteboard")
        self.resize(1180, 720)
        self.canvas = BoardCanvas()
        self.setCentralWidget(self.canvas)
        self.canvas.changed.connect(self._page_label)
        tb = QToolBar("Tools")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.tb = tb
        grp = QActionGroup(self)
        self.tool_actions = {}
        for name in ("pen", "highlighter", "eraser", "line", "arrow", "rect", "ellipse", "text"):
            a = QAction(f"{name.capitalize()} ({TOOL_KEYS[name]})", self, checkable=True)
            a.setShortcut(TOOL_KEYS[name])
            a.triggered.connect(lambda _=False, n=name: self.canvas.set_tool(n))
            grp.addAction(a)
            tb.addAction(a)
            self.tool_actions[name] = a
        self.tool_actions["pen"].setChecked(True)
        tb.addSeparator()
        self.swatches = []
        for c in PALETTE:
            pm = QPixmap(18, 18)
            pm.fill(QColor(c))
            a = QAction(QIcon(pm), c, self)
            a.setToolTip(f"Colour {c}")
            a.triggered.connect(lambda _=False, col=c: self.set_color(col))
            tb.addAction(a)
            self.swatches.append((a, c))
        more = QAction("Colour...", self)
        more.triggered.connect(self.pick_color)
        tb.addAction(more)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(1, 24)
        self.size_slider.setValue(4)
        self.size_slider.setFixedWidth(100)
        self.size_slider.setToolTip("Pen size")
        self.size_slider.valueChanged.connect(lambda v: setattr(self.canvas, "pen_w", float(v)))
        tb.addWidget(self.size_slider)
        tb.addSeparator()
        self.bg = QComboBox()
        self.bg.addItems(list(BACKGROUNDS))
        self.bg.currentTextChanged.connect(self._set_bg)
        tb.addWidget(self.bg)
        self.addToolBarBreak(Qt.TopToolBarArea)           # second row: undo / pages / export
        tb = QToolBar("Page")
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)
        for text, key, fn in (("Undo", "Ctrl+Z", self.canvas.undo), ("Redo", "Ctrl+Y", self.canvas.redo),
                              ("Clear page", "Ctrl+Shift+Delete", self._confirm_clear)):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(fn)
            tb.addAction(a)
        tb.addSeparator()
        for text, key, fn in (("< Prev", "PgUp", lambda: self.canvas.go(-1)), ("Next >", "PgDown", lambda: self.canvas.go(1)),
                              ("New page", "Ctrl+N", self.canvas.new_page)):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(fn)
            tb.addAction(a)
        self.page_lbl = QLabel(" Page 1/1 ")
        tb.addWidget(self.page_lbl)
        tb.addSeparator()
        for text, fn in (("Save PNG", self.save_png), ("Save PDF", self.save_pdf)):
            a = QAction(text, self)
            a.triggered.connect(fn)
            tb.addAction(a)
        ai = QAction("Board AI...", self)
        ai.setToolTip("Read handwriting, explain the board, finish a sketch, export lecture notes")
        ai.triggered.connect(self.open_ai)
        tb.addAction(ai)
        fs = QAction("Fullscreen (F11)", self)
        fs.setShortcut("F11")
        fs.triggered.connect(self.toggle_fullscreen)
        tb.addAction(fs)
        self.addAction(fs)

    # ---- actions
    def set_color(self, c):
        self.canvas.color = c
        if self.canvas.tool in ("eraser",):
            self.tool_actions["pen"].trigger()

    def pick_color(self):
        c = QColorDialog.getColor(QColor(self.canvas.color), self, "Pen colour")
        if c.isValid():
            self.set_color(c.name())

    def _set_bg(self, name):
        self.canvas.bg_name = name
        # auto-switch pen colour so it stays visible
        dark = QColor(BACKGROUNDS[name]).lightness() < 128
        if dark and QColor(self.canvas.color).lightness() < 60:
            self.canvas.color = "#ffffff"
        if not dark and QColor(self.canvas.color).lightness() > 220:
            self.canvas.color = "#111111"
        self.canvas.update()

    def _confirm_clear(self):
        if not self.canvas.model.items or QMessageBox.question(
                self, "Clear page", "Clear everything on this page? (You can undo.)") == QMessageBox.Yes:
            self.canvas.clear_page()

    def _page_label(self):
        self.page_lbl.setText(f" Page {self.canvas.page + 1}/{len(self.canvas.pages)} ")

    def open_ai(self):
        from .board_ai_ui import BoardAIDialog
        if getattr(self, "ai_dialog", None) is None:
            self.ai_dialog = BoardAIDialog(self.canvas, self)
        self.ai_dialog.show()
        self.ai_dialog.raise_()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def save_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save page", "whiteboard.png", "PNG image (*.png)")
        if path:
            self.canvas.render_image(scale=2.0).save(path, "PNG")

    def save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save all pages", "whiteboard.pdf", "PDF (*.pdf)")
        if path:
            n = self.canvas.export_pdf(path)
            QMessageBox.information(self, "Saved", f"Saved {n} page(s).")
