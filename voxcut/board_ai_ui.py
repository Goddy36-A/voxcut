"""'Board AI' window for the whiteboard: read handwriting, explain the board, finish a sketch, export lecture notes."""
from __future__ import annotations

import json

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSettings, QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from . import board_ai as B
from .drawing import Item

MAX_UPLOAD = 3_000_000   # bytes of PNG


def png_for_upload(canvas, max_bytes: int = MAX_UPLOAD):
    """Render the current board page to PNG, shrinking until it fits. -> (png_bytes, width, height, scale)"""
    scale = 1.0
    while True:
        img: QImage = canvas.render_image(scale=scale)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        if len(ba) <= max_bytes or scale <= 0.4:
            return bytes(ba), img.width(), img.height(), scale
        scale *= 0.75


class _Worker(QThread):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, client, png, tasks, lens, context):
        super().__init__()
        self.a = (client, png, tasks, lens, context)

    def run(self):
        c, png, tasks, lens, ctx = self.a
        try:
            self.done.emit(c.analyze(png, tasks, lens, ctx))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class BoardAIDialog(QDialog):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowTitle("Board AI - read, explain and finish your board")
        self.resize(760, 640)
        self.qs = QSettings("Ideawood", "IdeawoodStudio")
        self.worker = None
        self.last = {}
        self.sent = (0, 0, 1.0)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.key = QLineEdit(self.qs.value("board_key", ""))
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Board AI key")
        self.remember = QCheckBox("Remember on this PC")
        self.remember.setChecked(bool(self.qs.value("board_key", "")))
        kr = QHBoxLayout()
        kr.addWidget(self.key, 1)
        kr.addWidget(self.remember)
        form.addRow("Board AI key", kr)
        self.context = QLineEdit()
        self.context.setPlaceholderText("Topic, e.g. Calculus: derivatives (helps the AI read and explain correctly)")
        form.addRow("Topic", self.context)
        self.lens = QComboBox()
        self.lens.setEditable(True)
        self.lens.addItems(B.LENSES)
        form.addRow("Teaching style", self.lens)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.btns = {}
        for key, text, tasks in (("read", "Read handwriting", [B.TASK_READ]),
                                 ("explain", "Explain the board", [B.TASK_EXPLAIN]),
                                 ("both", "Read + explain", [B.TASK_READ, B.TASK_EXPLAIN]),
                                 ("finish", "Finish my sketch", [B.TASK_FINISH])):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, t=tasks: self.run_tasks(t))
            row.addWidget(b)
            self.btns[key] = b
        lay.addLayout(row)
        self.status = QLabel("Draw or write on the board, then choose what the AI should do. Only the current page "
                             "image is sent.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        # handwriting
        w = QWidget()
        v = QVBoxLayout(w)
        self.t_text = QTextEdit()
        self.t_text.setPlaceholderText("Handwriting as text")
        self.t_latex = QTextEdit()
        self.t_latex.setPlaceholderText("Formulas as LaTeX")
        cp = QPushButton("Copy LaTeX")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.t_latex.toPlainText()))
        v.addWidget(QLabel("Text"))
        v.addWidget(self.t_text)
        v.addWidget(QLabel("Math (LaTeX)"))
        v.addWidget(self.t_latex)
        v.addWidget(cp)
        self.tabs.addTab(w, "Handwriting")
        # explanation
        self.e_view = QTextBrowser()
        self.tabs.addTab(self.e_view, "Explanation and quiz")
        # sketch
        w = QWidget()
        v = QVBoxLayout(w)
        self.s_img = QLabel("No finished sketch yet.")
        self.s_img.setAlignment(Qt.AlignCenter)
        self.s_img.setMinimumHeight(220)
        self.s_info = QLabel("")
        self.s_add = QPushButton("Add the finished shapes to a NEW page")
        self.s_add.setEnabled(False)
        self.s_add.clicked.connect(self.add_shapes)
        v.addWidget(self.s_img, 1)
        v.addWidget(self.s_info)
        v.addWidget(self.s_add)
        self.tabs.addTab(w, "Finished sketch")
        # raw
        self.raw = QTextEdit()
        self.raw.setReadOnly(True)
        self.tabs.addTab(self.raw, "Raw reply")
        bottom = QHBoxLayout()
        self.save_btn = QPushButton("Save lecture notes (.md)...")
        self.save_btn.clicked.connect(self.save_notes)
        bottom.addWidget(self.save_btn)
        bottom.addStretch(1)
        lay.addLayout(bottom)

    # ---- running
    def run_tasks(self, tasks):
        if self.worker is not None:
            return
        client = B.BoardClient(self.key.text())
        if not client.api_key:
            QMessageBox.information(self, "Board AI", "Paste your Board AI key first.")
            return
        if self.remember.isChecked():
            self.qs.setValue("board_key", client.api_key)
        else:
            self.qs.remove("board_key")
        png, w, h, scale = png_for_upload(self.canvas)
        self.sent = (w, h, scale)
        for b in self.btns.values():
            b.setEnabled(False)
        self.status.setText(f"Sending the board ({len(png) // 1024} KB) ...")
        self.worker = _Worker(client, png, tasks, self.lens.currentText().strip() or "socratic",
                              self.context.text().strip())
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _finish_run(self):
        self.worker = None
        for b in self.btns.values():
            b.setEnabled(True)

    def _failed(self, msg):
        self._finish_run()
        self.status.setText("Failed: " + msg)
        QMessageBox.warning(self, "Board AI", msg)

    def _done(self, res):
        self._finish_run()
        self.last = res
        self.raw.setPlainText(json.dumps(res, indent=2, ensure_ascii=False))
        t, e, f = B.parse_transcription(res), B.parse_explanation(res), B.parse_finish(res)
        got = []
        if t["text"] or t["latex"]:
            self.t_text.setPlainText(t["text"])
            self.t_latex.setPlainText(t["latex"])
            got.append("handwriting")
            self.tabs.setCurrentIndex(0)
        if e["summary"] or e["title"] or e["quiz"] or e["key_points"]:
            md = [f"## {e['title']}" if e["title"] else "", e["summary"], ""]
            if e["key_points"]:
                md += ["### Key points"] + [f"- {p}" for p in e["key_points"]] + [""]
            if e["quiz"]:
                md += ["### Quiz"] + [f"{i}. {q}" for i, q in enumerate(e["quiz"], 1)]
            self.e_view.setMarkdown("\n".join(md))
            got.append("explanation")
            self.tabs.setCurrentIndex(1)
        if f["preview"] or f["shapes"]:
            self._show_sketch(f)
            got.append("finished sketch")
            self.tabs.setCurrentIndex(2)
        if got:
            self.status.setText("Done: " + ", ".join(got) + ".")
        else:
            self.status.setText("The AI replied, but in a format this version does not recognise - see 'Raw reply'.")
            self.tabs.setCurrentIndex(3)

    # ---- sketch
    def _show_sketch(self, f):
        self._shapes = f["shapes"]
        if f["preview"]:
            pm = QPixmap()
            if pm.loadFromData(f["preview"]):
                self.s_img.setPixmap(pm.scaledToWidth(min(560, pm.width()), Qt.SmoothTransformation))
        w, h, _ = self.sent
        n = len(B.shapes_to_items(self._shapes, w, h))
        self.s_info.setText(f"{len(self._shapes)} editable shape(s) returned, {n} can be placed on the whiteboard.")
        self.s_add.setEnabled(n > 0)

    def add_shapes(self):
        w, h, scale = self.sent
        items = B.shapes_to_items(getattr(self, "_shapes", []), w, h)
        if not items:
            return
        self.canvas.new_page()
        for it in items:
            pts = tuple((x / scale, y / scale) for x, y in it.pts)
            self.canvas.model.add(Item(it.kind, pts, it.color, it.width, it.text))
        self.canvas.update()
        self.status.setText(f"Added {len(items)} shape(s) on a new page. Edit them with the whiteboard tools.")

    # ---- notes
    def save_notes(self):
        t = {"text": self.t_text.toPlainText(), "latex": self.t_latex.toPlainText()}
        e = B.parse_explanation(self.last) if self.last else {}
        md = B.notes_markdown(self.context.text().strip(), t, e)
        path, _ = QFileDialog.getSaveFileName(self, "Save lecture notes", "board-notes.md", "Markdown (*.md)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
            self.status.setText(f"Saved {path}")
