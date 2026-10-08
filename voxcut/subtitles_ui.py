"""'Subtitles' tab: offline speech recognition -> editable captions -> SRT / VTT / TXT, soft track, or burned into video."""
from __future__ import annotations

import os
import re
import tempfile

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QWidget)

from . import asr
from .engine import Job, media_info

CUSTOM = "Use a model file I already have..."
VIDEO_FILTER = "Video or audio (*.mp4 *.mov *.mkv *.avi *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg);;All files (*)"


def parse_time(s: str):
    m = re.match(r"^\s*(?:(\d+):)?(\d+):(\d+)(?:[.,](\d{1,3}))?\s*$", s or "")
    if not m:
        return None
    h, mi, se, ms = int(m[1] or 0), int(m[2]), int(m[3]), int((m[4] or "0").ljust(3, "0"))
    return h * 3600 + mi * 60 + se + ms / 1000.0


class _Worker(QThread):
    status = Signal(str)
    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn, self.stop = fn, False

    def run(self):
        try:
            self.done.emit(self.fn(self))
        except asr.Cancelled:
            self.failed.emit("Cancelled")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class SubtitlePanel(QObject):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.qs: QSettings = main.qs
        self.src = ""
        self.tr: asr.Transcript | None = None
        self.cues: list[asr.Cue] = []
        self.worker: _Worker | None = None
        self._filling = False
        self.page = self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        from . import gui as G
        f1, l1 = G.card("1. Choose the video or audio", "Works offline on this PC. Tip: a recording from the Lecture "
                        "recorder can be sent here with one click.")
        self.path = QLineEdit()
        self.path.setPlaceholderText("Drop or browse a video / audio file")
        self.path.editingFinished.connect(lambda: self.load_file(self.path.text().strip(), quiet=True))
        b = QPushButton("Browse...")
        b.clicked.connect(self.browse)
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.addWidget(self.path, 1); h.addWidget(b)
        l1.addWidget(w)

        f2, l2 = G.card("2. Speech recognition", "The speech model is downloaded once (75-466 MB); after that "
                        "everything works without internet. Whisper does not support Luganda.")
        self.model = G.combo([], "")
        self.lang = G.combo(list(asr.LANGUAGES), "English")
        self.hint = QLineEdit()
        self.hint.setPlaceholderText("Names / terms to recognise, e.g. Ideawood, Kitara, photosynthesis (optional)")
        self.model_status = QLabel("")
        self.model_status.setObjectName("muted")
        self.model_status.setWordWrap(True)
        self.dl_btn = QPushButton("Download this model")
        self.dl_btn.clicked.connect(self.download)
        self.model.currentIndexChanged.connect(self._model_changed)
        l2.addWidget(G.row("Model", self.model))
        l2.addWidget(self.model_status)
        l2.addWidget(self.dl_btn)
        l2.addWidget(G.row("Language", self.lang))
        l2.addWidget(G.row("Vocabulary hint", self.hint))
        self.go = QPushButton("Create subtitles")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.create)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        self.bar = QProgressBar(); self.bar.setRange(0, 100)
        self.status = QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        for x in (self.go, self.cancel_btn, self.bar, self.status):
            l2.addWidget(x)

        f3, l3 = G.card("3. Check and fix the captions", "Click a cell to edit. Use | for a line break. "
                        "Times look like 00:01:23,500.")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "Start", "End", "Text"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 36); self.table.setColumnWidth(1, 100); self.table.setColumnWidth(2, 100)
        self.table.setMinimumHeight(260)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(self._cell_changed)
        l3.addWidget(self.table)
        row1 = QHBoxLayout()
        for text, fn in (("Merge with next", self.merge), ("Split", self.split), ("Delete", self.delete_row)):
            bb = QPushButton(text); bb.clicked.connect(fn); row1.addWidget(bb)
        l3.addLayout(row1)
        self.shift = QDoubleSpinBox(); self.shift.setRange(-30, 30); self.shift.setSingleStep(0.1); self.shift.setSuffix(" s")
        sb = QPushButton("Shift all times"); sb.clicked.connect(self.shift_all)
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.addWidget(self.shift); h.addWidget(sb)
        l3.addWidget(G.row("Fix timing", w))
        self.find = QLineEdit(); self.repl = QLineEdit()
        rb = QPushButton("Replace all"); rb.clicked.connect(self.replace_all)
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.find); h.addWidget(QLabel("->")); h.addWidget(self.repl); h.addWidget(rb)
        l3.addWidget(G.row("Find / replace", w))
        self.maxchars = QSpinBox(); self.maxchars.setRange(16, 60); self.maxchars.setValue(42)
        self.maxlines = QSpinBox(); self.maxlines.setRange(1, 2); self.maxlines.setValue(2)
        rs = QPushButton("Re-split captions"); rs.clicked.connect(self.resplit)
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("letters per line")); h.addWidget(self.maxchars)
        h.addWidget(QLabel("lines")); h.addWidget(self.maxlines); h.addWidget(rs)
        l3.addWidget(G.row("Caption length", w))

        f4, l4 = G.card("4. Save or add to the video")
        row = QHBoxLayout()
        for text, fn in (("Save SRT", lambda: self.save("srt")), ("Save VTT (web)", lambda: self.save("vtt")),
                         ("Save transcript (TXT)", lambda: self.save("txt"))):
            bb = QPushButton(text); bb.clicked.connect(fn); row.addWidget(bb)
        l4.addLayout(row)
        self.pos = G.combo(list(asr.POSITIONS), "Bottom")
        self.size = G.combo(list(asr.SIZES), "Medium")
        self.color = G.combo(list(asr.COLORS), "White")
        self.box = QCheckBox("Dark box behind the text (easier to read)"); self.box.setChecked(True)
        self.hl = QCheckBox("Highlight each word as it is spoken (social-media style)")
        for lab, wd in (("Position", self.pos), ("Text size", self.size), ("Text colour", self.color)):
            l4.addWidget(G.row(lab, wd))
        l4.addWidget(self.box); l4.addWidget(self.hl)
        self.burn_btn = QPushButton("Burn subtitles into a new video...")
        self.burn_btn.clicked.connect(self.burn)
        self.embed_btn = QPushButton("Add as a selectable subtitle track (MP4, no re-encode)...")
        self.embed_btn.clicked.connect(self.embed)
        self.out_status = QLabel(""); self.out_status.setObjectName("muted"); self.out_status.setWordWrap(True)
        self.open_btn = QPushButton("Open folder"); self.open_btn.hide()
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self._last_out))))
        self._last_out = ""
        for x in (self.burn_btn, self.embed_btn, self.out_status, self.open_btn):
            l4.addWidget(x)
        self._fill_models()
        return G.make_page(f1, f2, f3, f4)

    # ------------------------------------------------------------------ model handling
    def _fill_models(self):
        self.model.blockSignals(True)
        cur = self.qs.value("asr_model", "base.en")
        self.model.clear()
        inst = set(asr.installed_models())
        for key, (label, _f, _mb, _ml) in asr.MODELS.items():
            tag = " - built in" if asr.is_bundled(key) else ""
            self.model.addItem(("✓ " if key in inst else "") + label + tag, key)
        self.model.addItem(CUSTOM, "custom")
        i = self.model.findData(cur)
        self.model.setCurrentIndex(max(i, 0))
        self.model.blockSignals(False)
        self._model_changed()

    def _model_key(self):
        return self.model.currentData()

    def _model_path(self):
        k = self._model_key()
        if k == "custom":
            return self.qs.value("asr_custom_model", "")
        return asr.find_model(k) or ""

    def _model_changed(self, *_):
        k = self._model_key()
        self.qs.setValue("asr_model", k)
        if k == "custom":
            p = self.qs.value("asr_custom_model", "")
            if not (p and os.path.isfile(p)):
                p, _ = QFileDialog.getOpenFileName(self.main, "Choose a whisper model file (ggml-*.bin)", "",
                                                   "Whisper model (*.bin);;All files (*)")
                if p:
                    self.qs.setValue("asr_custom_model", p)
            self.model_status.setText(f"Using: {p}" if p else "No model file chosen yet.")
            self.dl_btn.hide()
            return
        ok = k in asr.installed_models()
        self.dl_btn.setVisible(not ok)
        mb, multi = asr.MODELS[k][2], asr.MODELS[k][3]
        self.model_status.setText(("Built into Ideawood Studio - ready to use, no download. " if asr.is_bundled(k)
                                   else "Installed - ready to use. " if ok else f"Not downloaded yet (about {mb} MB). ")
                                  + ("Understands many languages." if multi else "English only."))

    def download(self):
        k = self._model_key()
        if k == "custom" or self.worker:
            return
        self._busy(True, f"Downloading the speech model ({asr.MODELS[k][2]} MB)...")
        self._start(lambda w: asr.download_model(k, w.progress.emit, lambda: w.stop), self._downloaded)

    def _downloaded(self, _path):
        self._busy(False, "Model downloaded - you can now create subtitles.")
        self._fill_models()

    # ------------------------------------------------------------------ source file
    def browse(self):
        p, _ = QFileDialog.getOpenFileName(self.main, "Choose video or audio", self.qs.value("last_dir", ""), VIDEO_FILTER)
        if p:
            self.load_file(p)

    def load_file(self, path: str, quiet=False):
        if not path:
            return
        if not os.path.isfile(path):
            if not quiet:
                QMessageBox.information(self.main, "Ideawood Studio", "That file was not found.")
            return
        self.src = path
        self.path.setText(path)
        self.qs.setValue("last_dir", os.path.dirname(path))
        self.status.setText("Ready. Choose the model and language, then press Create subtitles.")

    # ------------------------------------------------------------------ running
    def _busy(self, on: bool, msg: str = ""):
        for b in (self.go, self.dl_btn, self.burn_btn, self.embed_btn):
            b.setEnabled(not on)
        self.cancel_btn.setEnabled(on)
        if msg:
            self.status.setText(msg)
        if not on:
            self.worker = None
        else:
            self.bar.setValue(0)

    def cancel(self):
        w = self.worker
        if w:
            w.stop = True
            hook = getattr(w, "stop_hook", None)
            if hook:
                hook.cancel()

    def _start(self, fn, on_done):
        w = _Worker(fn)
        self.worker = w
        w.status.connect(self.status.setText)
        w.progress.connect(lambda p: self.bar.setValue(int(p)))
        w.done.connect(on_done)
        w.failed.connect(self._failed)
        w.start()

    def _failed(self, msg):
        self._busy(False, "Stopped: " + msg)
        if msg != "Cancelled":
            QMessageBox.warning(self.main, "Ideawood Studio", msg)

    def create(self):
        if self.worker:
            return
        if not self.src:
            self.load_file(self.path.text().strip())
        if not self.src:
            QMessageBox.information(self.main, "Ideawood Studio", "Choose a video or audio file first.")
            return
        mp = self._model_path()
        k = self._model_key()
        if not mp:
            QMessageBox.information(self.main, "Ideawood Studio",
                                    "Download the speech model first (button above), or choose a model file you already have.")
            return
        code = asr.LANGUAGES[self.lang.currentText()]
        multi = True if k == "custom" else asr.MODELS[k][3]
        if not multi:
            if code not in ("en", "auto"):
                QMessageBox.information(self.main, "Ideawood Studio",
                                        "This model understands English only. For other languages choose a "
                                        "'99 languages' model.")
                return
            code = "en"
        eng = asr.WhisperEngine(mp)
        src, hint = self.src, self.hint.text()
        self._busy(True, "Starting...")

        def job(w):
            return asr.transcribe_file(src, eng, code, hint, w.status.emit, w.progress.emit, lambda: w.stop)
        self._start(job, self._transcribed)

    def _transcribed(self, tr: asr.Transcript):
        self.tr = tr
        info = media_info(self.src)
        self.maxchars.setValue(asr.auto_max_chars(info["w"], info["h"], self.size.currentText()) if info["has_video"] else 42)
        self._make_cues()
        self._busy(False, f"Done: {len(self.cues)} captions"
                   + (f" (language: {tr.language})" if tr.language else "")
                   + ". Check them below, then save or add them to the video.")
        if not self.cues:
            self.status.setText(self.status.text() + " No speech was found - check the language and the audio.")

    def _make_cues(self):
        self.cues = asr.make_cues(self.tr, max_chars=self.maxchars.value(), max_lines=self.maxlines.value())
        self._fill_table()

    def resplit(self):
        if not self.tr:
            return
        if self._dirty() and QMessageBox.question(self.main, "Re-split", "This rebuilds the captions and drops manual "
                                                  "edits. Continue?") != QMessageBox.Yes:
            return
        self._make_cues()

    # ------------------------------------------------------------------ table <-> cues
    def _fill_table(self):
        self._filling = True
        self.table.setRowCount(len(self.cues))
        for i, c in enumerate(self.cues):
            vals = [str(i + 1), asr._ts(c.start, True), asr._ts(c.end, True), c.text.replace("\n", " | ")]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 0:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, j, it)
        self._filling = False
        self._orig = [(c.text, [w for w in c.words]) for c in self.cues]

    def _dirty(self) -> bool:
        try:
            return [c.text for c in self.read_cues()] != [t for t, _ in self._orig]
        except Exception:  # noqa: BLE001
            return True

    def read_cues(self) -> list:
        out = []
        for i in range(self.table.rowCount()):
            s = parse_time(self.table.item(i, 1).text())
            e = parse_time(self.table.item(i, 2).text())
            txt = self.table.item(i, 3).text().replace(" | ", "\n").replace("|", "\n").strip()
            if s is None or e is None or e <= s or not txt:
                continue
            words = self._orig[i][1] if i < len(getattr(self, "_orig", [])) and self._orig[i][0] == txt else []
            out.append(asr.Cue(s, e, txt, list(words)))
        return out

    def _cell_changed(self, item):
        if self._filling:
            return
        if item.column() in (1, 2):
            v = parse_time(item.text())
            if v is None:
                self._filling = True
                c = self.cues[item.row()] if item.row() < len(self.cues) else None
                item.setText(asr._ts(c.start if item.column() == 1 else c.end, True) if c else "00:00:00,000")
                self._filling = False
                self.status.setText("Times must look like 00:01:23,500")
            else:
                self._filling = True
                item.setText(asr._ts(v, True))
                self._filling = False

    def _sync(self):
        """Pull the table into self.cues (keeps karaoke words only for unedited rows)."""
        self.cues = self.read_cues()
        self._fill_table()

    def merge(self):
        r = self.table.currentRow()
        cues = self.read_cues()
        if 0 <= r < len(cues) - 1:
            a, b = cues[r], cues[r + 1]
            cues[r:r + 2] = [asr.Cue(a.start, b.end, a.text.replace("\n", " ") + " " + b.text.replace("\n", " "),
                                     a.words + b.words if a.words and b.words else [])]
            self.cues = cues
            self._fill_table()

    def split(self):
        r = self.table.currentRow()
        cues = self.read_cues()
        if not (0 <= r < len(cues)):
            return
        c = cues[r]
        words = c.text.replace("\n", " ").split()
        if len(words) < 2:
            return
        k = len(words) // 2
        mid = c.start + (c.end - c.start) * k / len(words)
        cues[r:r + 1] = [asr.Cue(c.start, mid, " ".join(words[:k])), asr.Cue(mid, c.end, " ".join(words[k:]))]
        self.cues = cues
        self._fill_table()

    def delete_row(self):
        r = self.table.currentRow()
        cues = self.read_cues()
        if 0 <= r < len(cues):
            del cues[r]
            self.cues = cues
            self._fill_table()

    def shift_all(self):
        d = self.shift.value()
        cues = self.read_cues()
        for c in cues:
            c.start, c.end = max(0.0, c.start + d), max(0.1, c.end + d)
            for w in c.words:
                w.start, w.end = max(0.0, w.start + d), max(0.0, w.end + d)
        self.cues = cues
        self._fill_table()

    def replace_all(self):
        a = self.find.text()
        if not a:
            return
        n = 0
        cues = self.read_cues()
        for c in cues:
            if a in c.text:
                n += c.text.count(a)
                c.text = c.text.replace(a, self.repl.text())
                c.words = []                     # text changed: word timings no longer match
        self.cues = cues
        self._fill_table()
        self.status.setText(f"Replaced {n} occurrence(s).")

    # ------------------------------------------------------------------ exports
    def _base(self):
        return os.path.splitext(self.src)[0] if self.src else os.path.join(os.path.expanduser("~"), "subtitles")

    def save(self, kind: str):
        cues = self.read_cues()
        if not cues:
            QMessageBox.information(self.main, "Ideawood Studio", "Create or add some captions first.")
            return
        flt = {"srt": "SubRip (*.srt)", "vtt": "WebVTT (*.vtt)", "txt": "Text (*.txt)"}[kind]
        p, _ = QFileDialog.getSaveFileName(self.main, "Save", self._base() + "." + kind, flt)
        if not p:
            return
        txt = {"srt": asr.to_srt, "vtt": asr.to_vtt, "txt": asr.to_txt}[kind](cues)
        with open(p, "w", encoding="utf-8-sig" if kind == "srt" else "utf-8") as f:
            f.write(txt)
        self._saved(p)

    def _saved(self, p):
        self._last_out = p
        self.out_status.setText("Saved: " + p)
        self.open_btn.show()

    def _need_video(self):
        if not self.src or not os.path.isfile(self.src):
            QMessageBox.information(self.main, "Ideawood Studio", "Choose the video file first.")
            return None
        info = media_info(self.src)
        if not info["has_video"]:
            QMessageBox.information(self.main, "Ideawood Studio", "This is an audio file - save the SRT/VTT instead.")
            return None
        if not self.read_cues():
            QMessageBox.information(self.main, "Ideawood Studio", "Create or add some captions first.")
            return None
        return info

    def burn(self):
        info = self._need_video()
        if not info or self.worker:
            return
        out, _ = QFileDialog.getSaveFileName(self.main, "Save video with subtitles", self._base() + " (subtitled).mp4",
                                             "MP4 video (*.mp4)")
        if not out:
            return
        cues = self.read_cues()
        ass = asr.to_ass(cues, info["w"], info["h"], self.pos.currentText(), self.size.currentText(),
                         self.color.currentText(), self.box.isChecked(), self.hl.isChecked())
        src, dur = self.src, info["duration"]
        self._busy(True, "Adding subtitles to the video...")

        def job(w):
            work = tempfile.mkdtemp(prefix="ideawood_sub_")
            try:
                with open(os.path.join(work, "subs.ass"), "w", encoding="utf-8") as f:
                    f.write(ass)
                j = Job(asr.burn_cmd(src, "subs.ass", out), dur, cwd=work)
                w.stop_hook = j
                j.run(w.progress.emit)
                return out
            finally:
                import shutil
                shutil.rmtree(work, ignore_errors=True)
        self._start(job, self._exported)

    def embed(self):
        info = self._need_video()
        if not info or self.worker:
            return
        out, _ = QFileDialog.getSaveFileName(self.main, "Save video with subtitle track", self._base() + " (subtitles).mp4",
                                             "MP4 video (*.mp4)")
        if not out:
            return
        srt = asr.to_srt(self.read_cues())
        lang = {"English": "eng", "Swahili": "swa", "French": "fra"}.get(self.lang.currentText(), "und")
        src, dur = self.src, info["duration"]
        self._busy(True, "Adding the subtitle track...")

        def job(w):
            work = tempfile.mkdtemp(prefix="ideawood_sub_")
            try:
                p = os.path.join(work, "s.srt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write(srt)
                Job(asr.embed_cmd(src, p, out, lang), dur).run(w.progress.emit)
                return out
            finally:
                import shutil
                shutil.rmtree(work, ignore_errors=True)
        self._start(job, self._exported)

    def _exported(self, out):
        self._busy(False, "Done.")
        self.bar.setValue(100)
        self._saved(out)
