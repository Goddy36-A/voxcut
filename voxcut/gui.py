import os
import sys

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox, QTabWidget,
                               QTextEdit, QVBoxLayout, QWidget)

from . import __version__
from .engine import (CODECS, COMPRESSION, FIT_MODES, ORIENTATIONS, QUALITIES, Job, Settings, build_command)

VIDEO_FILTER = "Videos (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.mts *.ts);;All files (*)"


class Worker(QThread):
    progress = Signal(int, float)      # file index, percent
    file_done = Signal(int, str)       # file index, message ("OK" or error)
    all_done = Signal()

    def __init__(self, files, outdir, settings, suffix):
        super().__init__()
        self.files, self.outdir, self.settings, self.suffix = files, outdir, settings, suffix
        self.job = None
        self.stop = False

    def run(self):
        for i, f in enumerate(self.files):
            if self.stop:
                break
            base = os.path.splitext(os.path.basename(f))[0]
            out = os.path.join(self.outdir or os.path.dirname(f), f"{base}{self.suffix}.mp4")
            try:
                cmd, dur = build_command(f, out, self.settings)
                self.job = Job(cmd, dur)
                self.job.run(lambda p, i=i: self.progress.emit(i, p))
                self.file_done.emit(i, "OK")
            except Exception as e:  # noqa: BLE001
                self.file_done.emit(i, str(e))
        self.all_done.emit()

    def cancel(self):
        self.stop = True
        if self.job:
            self.job.cancel()


class FilePick(QWidget):
    def __init__(self, filt):
        super().__init__()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("(none)")
        b = QPushButton("Browse…")
        c = QPushButton("Clear")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for w in (self.edit, b, c):
            lay.addWidget(w)
        b.clicked.connect(lambda: self.edit.setText(QFileDialog.getOpenFileName(self, "Select file", "", filt)[0] or self.edit.text()))
        c.clicked.connect(self.edit.clear)

    def value(self):
        return self.edit.text().strip() or None


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"VoxCut {__version__} – offline video & voice enhancer")
        self.resize(980, 720)
        self.worker = None
        self.bg_color = "black"

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)

        # ---- left: files ----
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Videos to process</b> (drag & drop or Add)"))
        self.listw = QListWidget()
        self.listw.setAcceptDrops(True)
        left.addWidget(self.listw, 1)
        row = QHBoxLayout()
        for text, fn in (("Add videos…", self.add_files), ("Remove selected", self.remove_sel), ("Clear", self.listw.clear)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        left.addLayout(row)
        outrow = QHBoxLayout()
        self.outdir = QLineEdit()
        self.outdir.setPlaceholderText("Output folder (default: next to each video)")
        ob = QPushButton("Output folder…")
        ob.clicked.connect(lambda: self.outdir.setText(QFileDialog.getExistingDirectory(self, "Output folder") or self.outdir.text()))
        outrow.addWidget(self.outdir)
        outrow.addWidget(ob)
        left.addLayout(outrow)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        left.addWidget(self.log)
        self.bar = QProgressBar()
        left.addWidget(self.bar)
        brow = QHBoxLayout()
        self.go = QPushButton("▶  Process all")
        self.go.setStyleSheet("font-weight:bold;padding:8px;")
        self.go.clicked.connect(self.start)
        self.cancel = QPushButton("Cancel")
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(lambda: self.worker and self.worker.cancel())
        brow.addWidget(self.go, 3)
        brow.addWidget(self.cancel, 1)
        left.addLayout(brow)
        outer.addLayout(left, 5)

        # ---- right: tabs ----
        tabs = QTabWidget()
        tabs.addTab(self.audio_tab(), "Audio / Voice")
        tabs.addTab(self.video_tab(), "Format & Quality")
        tabs.addTab(self.extras_tab(), "Intro / Outro")
        outer.addWidget(tabs, 4)
        self.setAcceptDrops(True)

    # ---------- tabs ----------
    def audio_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.c_noise = QCheckBox("Remove background noise")
        self.c_noise.setChecked(True)
        self.s_noise = QSlider(Qt.Horizontal)
        self.s_noise.setRange(5, 30)
        self.s_noise.setValue(12)
        self.l_noise = QLabel("12 dB")
        self.s_noise.valueChanged.connect(lambda v: self.l_noise.setText(f"{v} dB"))
        self.c_voice = QCheckBox("Boost voice (clarity + warmth EQ)")
        self.c_voice.setChecked(True)
        self.c_comp = QCheckBox("Even out volume (compressor)")
        self.c_comp.setChecked(True)
        self.c_norm = QCheckBox("Increase / normalize loudness")
        self.c_norm.setChecked(True)
        self.s_lufs = QSlider(Qt.Horizontal)
        self.s_lufs.setRange(-24, -9)
        self.s_lufs.setValue(-14)
        self.l_lufs = QLabel("-14 LUFS")
        self.s_lufs.valueChanged.connect(lambda v: self.l_lufs.setText(f"{v} LUFS"))
        self.s_gain = QSpinBox()
        self.s_gain.setRange(-20, 20)
        self.s_gain.setSuffix(" dB")
        self.c_audio_only = QCheckBox("Fix audio only (keep video untouched – fastest)")
        f.addRow(self.c_noise)
        r = QHBoxLayout(); r.addWidget(self.s_noise); r.addWidget(self.l_noise)
        f.addRow("Noise strength", r)
        f.addRow(self.c_voice)
        f.addRow(self.c_comp)
        f.addRow(self.c_norm)
        r = QHBoxLayout(); r.addWidget(self.s_lufs); r.addWidget(self.l_lufs)
        f.addRow("Target loudness", r)
        f.addRow("Extra gain", self.s_gain)
        f.addRow(self.c_audio_only)
        f.addRow(QLabel("<i>-14 LUFS = YouTube/Instagram standard. -16 is a bit quieter.<br>Louder = closer to -9.</i>"))
        return w

    def video_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.o_orient = QComboBox(); self.o_orient.addItems(ORIENTATIONS)
        self.o_quality = QComboBox(); self.o_quality.addItems(QUALITIES); self.o_quality.setCurrentText("1080p (Full HD)")
        self.o_fit = QComboBox(); self.o_fit.addItems(FIT_MODES)
        self.btn_col = QPushButton("Pick colour (black)")
        self.btn_col.clicked.connect(self.pick_color)
        self.bg_img = FilePick("Images (*.png *.jpg *.jpeg *.webp)")
        self.o_fps = QComboBox(); self.o_fps.addItems(["24", "25", "30", "50", "60"]); self.o_fps.setCurrentText("30")
        self.o_comp = QComboBox(); self.o_comp.addItems(COMPRESSION); self.o_comp.setCurrentText("Balanced")
        self.o_codec = QComboBox(); self.o_codec.addItems(CODECS)
        f.addRow("Orientation", self.o_orient)
        f.addRow("Resolution (HD conversion)", self.o_quality)
        f.addRow("When aspect differs", self.o_fit)
        f.addRow("Solid colour", self.btn_col)
        f.addRow("Background image", self.bg_img)
        f.addRow("Frame rate", self.o_fps)
        f.addRow("Compression", self.o_comp)
        f.addRow("Codec", self.o_codec)
        return w

    def extras_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.intro = FilePick(VIDEO_FILTER)
        self.outro = FilePick(VIDEO_FILTER)
        f.addRow("Intro video", self.intro)
        f.addRow("Outro video", self.outro)
        f.addRow(QLabel("<i>Intro/outro are resized to match, and joined automatically.<br>"
                        "Voice enhancement is applied to your main video only.</i>"))
        return w

    # ---------- actions ----------
    def pick_color(self):
        c = QColorDialog.getColor()
        if c.isValid():
            self.bg_color = c.name().replace("#", "0x")
            self.btn_col.setText(f"Colour {c.name()}")

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add videos", "", VIDEO_FILTER)
        self.listw.addItems(files)

    def remove_sel(self):
        for it in self.listw.selectedItems():
            self.listw.takeItem(self.listw.row(it))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.listw.addItems([u.toLocalFile() for u in e.mimeData().urls() if os.path.isfile(u.toLocalFile())])

    def settings(self) -> Settings:
        return Settings(
            noise_removal=self.c_noise.isChecked(), noise_strength=self.s_noise.value(),
            voice_boost=self.c_voice.isChecked(), compressor=self.c_comp.isChecked(),
            normalize=self.c_norm.isChecked(), target_lufs=float(self.s_lufs.value()),
            extra_gain_db=float(self.s_gain.value()), orientation=self.o_orient.currentText(),
            quality=self.o_quality.currentText(), fit_mode=self.o_fit.currentText(), bg_color=self.bg_color,
            bg_image=self.bg_img.value(), fps=int(self.o_fps.currentText()), compression=self.o_comp.currentText(),
            codec=self.o_codec.currentText(), intro=self.intro.value(), outro=self.outro.value(),
            audio_only_enhance=self.c_audio_only.isChecked())

    def start(self):
        files = [self.listw.item(i).text() for i in range(self.listw.count())]
        if not files:
            QMessageBox.information(self, "VoxCut", "Add at least one video first.")
            return
        s = self.settings()
        if s.fit_mode == "Image background" and not s.bg_image:
            QMessageBox.warning(self, "VoxCut", "Choose a background image or another fit mode.")
            return
        outdir = self.outdir.text().strip()
        self.log.clear()
        self.bar.setValue(0)
        self.go.setEnabled(False)
        self.cancel.setEnabled(True)
        self.total = len(files)
        self.worker = Worker(files, outdir, s, "_enhanced")
        self.worker.progress.connect(lambda i, p: self.bar.setValue(int((i + p / 100) / self.total * 100)))
        self.worker.file_done.connect(lambda i, m: self.log.append(f"[{i+1}/{self.total}] {'✔ done' if m == 'OK' else '✖ ' + m}"))
        self.worker.all_done.connect(self.finished)
        self.worker.start()

    def finished(self):
        self.go.setEnabled(True)
        self.cancel.setEnabled(False)
        self.log.append("Finished.")


def main():
    app = QApplication(sys.argv)
    w = Main()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
