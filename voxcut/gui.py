import ctypes
import os
import sys
import tempfile

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider,
                               QSpinBox, QStackedWidget, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget)

from . import __version__
from .cloud import CloudClient, CloudError
from .maker import FORMATS as MAKER_FORMATS, Cancelled, make_video
from .engine import (AUDIO_FORMATS, CODECS, COLOR_PRESETS, COMPRESSION, FIT_MODES, ORIENTATIONS, QUALITIES,
                     WM_POSITIONS, Job, Settings, plan_add_audio, plan_enhance, plan_extract_audio,
                     plan_remove_audio)
from .matting import MAT_MODES, MAT_QUALITY, available as matting_available
from .paths import asset_path
from .theme import ACCENT_PRESETS, THEMES, apply_theme

VIDEO_FILTER = "Videos (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.mts *.ts);;All files (*)"
AUDIO_FILTER = "Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.wma);;All files (*)"
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp)"

PRESETS = {
    "Custom": None,
    "YouTube - Full HD landscape": dict(orient="Landscape 16:9", quality="1080p (Full HD)", comp="Balanced", lufs=-14),
    "TikTok / Reels / Shorts - portrait": dict(orient="Portrait 9:16", quality="1080p (Full HD)", comp="Balanced", lufs=-14),
    "WhatsApp status - small file": dict(orient="Portrait 9:16", quality="720p (HD)", comp="Smallest file", lufs=-16),
    "Podcast / voice clean-up (audio only)": dict(audio_only=True, lufs=-16, strength=15),
    "Maximum quality - 4K archive": dict(orient="Keep original", quality="2160p (4K)",
                                         comp="Maximum quality (big file)", codec="H.265 / HEVC (smaller)"),
}


def keep_awake(on: bool):
    """Stop Windows sleeping while a long render is running."""
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if on else 0x80000000)
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- small UI helpers
def card(title, subtitle=None):
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 18)
    lay.setSpacing(11)
    t = QLabel(title)
    t.setObjectName("h")
    lay.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("muted")
        s.setWordWrap(True)
        lay.addWidget(s)
    return f, lay


def row(label, widget, tip=None):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(10)
    lab = QLabel(label)
    lab.setMinimumWidth(128)
    lab.setMaximumWidth(150)
    lab.setWordWrap(True)
    h.addWidget(lab)
    h.addWidget(widget, 1)
    if tip:
        w.setToolTip(tip)
    return w


def slider(lo, hi, val, fmt=lambda v: str(v)):
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    lab = QLabel()
    lab.setMinimumWidth(62)
    lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s.valueChanged.connect(lambda v: lab.setText(fmt(v)))
    lab.setText(fmt(val))
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(s, 1)
    h.addWidget(lab)
    return w, s


def combo(items, current=None):
    c = QComboBox()
    c.addItems(list(items))
    if current:
        c.setCurrentText(current)
    return c


def spin(lo, hi, val=0.0, suffix=" s", step=0.5):
    d = QDoubleSpinBox()
    d.setRange(lo, hi)
    d.setValue(val)
    d.setSuffix(suffix)
    d.setSingleStep(step)
    d.setDecimals(1)
    return d


class FilePick(QWidget):
    def __init__(self, filt, placeholder="(none)"):
        super().__init__()
        self.filt = filt
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        b = QPushButton("Browse")
        c = QPushButton("Clear")
        c.setObjectName("ghost")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for w in (self.edit, b, c):
            lay.addWidget(w)
        b.clicked.connect(self.pick)
        c.clicked.connect(self.edit.clear)

    def pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select file", "", self.filt)
        if p:
            self.edit.setText(p)

    def value(self):
        v = self.edit.text().strip()
        return v or None


def make_page(*cards):
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 8, 0)
    lay.setSpacing(12)
    for c in cards:
        lay.addWidget(c)
    lay.addStretch(1)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(inner)
    return sa


# --------------------------------------------------------------------------- background worker
class Worker(QThread):
    progress = Signal(int, float)
    item_done = Signal(int, str, str)     # task index, "OK"/error text, output path
    all_done = Signal()

    def __init__(self, tasks):
        super().__init__()
        self.tasks, self.job, self.stop = tasks, None, False

    def run(self):
        for i, (_label, thunk) in enumerate(self.tasks):
            if self.stop:
                break
            try:
                res = thunk()
                if len(res) == 3:                      # (ffmpeg argv, duration, output)
                    self.job, out = Job(res[0], res[1]), res[2]
                else:                                  # (runner, output) - e.g. background replacement chain
                    self.job, out = res
                self.job.run(lambda p, i=i: self.progress.emit(i, p))
                self.item_done.emit(i, "OK", out)
            except Exception as e:  # noqa: BLE001
                self.item_done.emit(i, str(e), "")
        self.all_done.emit()

    def cancel(self):
        self.stop = True
        if self.job:
            self.job.cancel()


class MakerWorker(QThread):
    status = Signal(str)
    progress = Signal(float)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, cloud, prompt, out, fmt, narrate, music):
        super().__init__()
        self.args = (cloud, prompt, out)
        self.kw = dict(fmt=fmt, narrate=narrate, music=music)
        self.stop = False

    def run(self):
        try:
            res = make_video(*self.args, on_status=self.status.emit, on_progress=self.progress.emit,
                             should_cancel=lambda: self.stop, **self.kw)
            self.finished_ok.emit(res)
        except Cancelled:
            self.failed.emit("Cancelled")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    def cancel(self):
        self.stop = True


# --------------------------------------------------------------------------- preview window
class PreviewDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preview")
        self.resize(900, 600)
        self.path, self.player = path, None
        lay = QVBoxLayout(self)
        self.note = QLabel("Preview is a quick low-resolution render (about 480p). The final file will be sharper.")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
            self.video = QVideoWidget()
            self.video.setMinimumHeight(380)
            lay.addWidget(self.video, 1)
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video)
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.errorOccurred.connect(self._error)
            self.player.positionChanged.connect(self._pos)
            self.player.durationChanged.connect(lambda d: self.seek.setRange(0, d))
        except Exception as e:  # noqa: BLE001
            self.player = None
            self.note.setText(f"Built-in player unavailable ({e}). Use 'Open in system player'.")
        lay.addWidget(self.note)
        ctl = QHBoxLayout()
        self.play = QPushButton("Pause")
        self.play.clicked.connect(self.toggle)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.sliderMoved.connect(lambda v: self.player and self.player.setPosition(v))
        ext = QPushButton("Open in system player")
        ext.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        for w in (self.play, self.seek, ext, close):
            ctl.addWidget(w, 1 if w is self.seek else 0)
        lay.addLayout(ctl)
        if self.player:
            self.player.play()

    def _pos(self, p):
        if not self.seek.isSliderDown():
            self.seek.setValue(p)

    def toggle(self):
        if not self.player:
            return
        if self.player.playbackState() == self.player.PlaybackState.PlayingState:
            self.player.pause()
            self.play.setText("Play")
        else:
            self.player.play()
            self.play.setText("Pause")

    def _error(self, *_):
        self.note.setText("The built-in player could not play this file - opening it in your default player instead.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def done(self, r):
        if self.player:
            self.player.stop()
        super().done(r)


# --------------------------------------------------------------------------- main window
class Main(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.qs = QSettings("VoxCut", "VoxCut")
        self.worker = None
        self.bg_color = "black"
        self.last_out_dir = ""
        self.results = []
        self.after = None
        self.pv_dialog = None
        self.pv_n = 0
        self.setWindowTitle(f"VoxCut {__version__}")
        self.setWindowIcon(QIcon(asset_path("icon.png")))
        self.resize(1260, 800)
        self.setMinimumSize(1000, 660)
        self.setAcceptDrops(True)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 16)
        outer.setSpacing(12)
        outer.addWidget(self._header())
        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._left(), 5)
        body.addWidget(self._right(), 6)
        outer.addLayout(body, 1)

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(QIcon(asset_path("icon.png")), self)
            m = QMenu()
            m.addAction("Show VoxCut", self._restore)
            m.addAction("Quit", self.app.quit)
            self.tray.setContextMenu(m)
            self.tray.activated.connect(lambda *_: self._restore())
            self.tray.show()
        self.restyle()

    # ---------------- layout
    def _header(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        logo = QLabel()
        logo.setPixmap(QPixmap(asset_path("icon.png")).scaled(46, 46, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        h.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel("VoxCut")
        t.setObjectName("title")
        st = QLabel("Offline video & voice studio")
        st.setObjectName("muted")
        col.addWidget(t)
        col.addWidget(st)
        h.addLayout(col)
        badge = QLabel(f"v{__version__}")
        badge.setObjectName("badge")
        h.addWidget(badge, 0, Qt.AlignVCenter)
        h.addStretch(1)
        h.addWidget(QLabel("Quick preset"))
        self.preset = combo(PRESETS)
        self.preset.setMinimumWidth(270)
        self.preset.activated.connect(self.apply_preset)
        h.addWidget(self.preset)
        return w

    def _left(self):
        f, lay = card("Queue", "Drag & drop videos here, or use Add. Everything runs locally on your PC.")
        self.listw = QListWidget()
        self.listw.setObjectName("queue")
        self.listw.setSelectionMode(QListWidget.ExtendedSelection)
        lay.addWidget(self.listw, 1)
        r = QHBoxLayout()
        for text, fn in (("Add videos", self.add_files), ("Remove", self.remove_sel), ("Clear", self.listw.clear)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            r.addWidget(b)
        lay.addLayout(r)
        orow = QHBoxLayout()
        self.outdir = QLineEdit()
        self.outdir.setPlaceholderText("Output folder (default: next to each video)")
        self.outdir.setText(self.qs.value("outdir", ""))
        ob = QPushButton("Choose")
        ob.clicked.connect(self.pick_outdir)
        orow.addWidget(self.outdir, 1)
        orow.addWidget(ob)
        lay.addLayout(orow)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(104)
        self.log.setPlaceholderText("Activity log")
        lay.addWidget(self.log)

        pr = QHBoxLayout()
        self.btn_preview = QPushButton("Preview")
        self.btn_preview.setToolTip("Quick low-res sample with all your settings (main video only).")
        self.btn_preview.clicked.connect(self.preview)
        self.p_start = spin(0, 36000, 0, " s", 1)
        self.p_len = spin(2, 60, 10, " s", 1)
        pr.addWidget(self.btn_preview)
        pr.addWidget(QLabel("from"))
        pr.addWidget(self.p_start)
        pr.addWidget(QLabel("for"))
        pr.addWidget(self.p_len)
        pr.addStretch(1)
        lay.addLayout(pr)

        self.bar = QProgressBar()
        self.bar.setValue(0)
        lay.addWidget(self.bar)
        self.status = QLabel("Ready")
        self.status.setObjectName("muted")
        lay.addWidget(self.status)
        br = QHBoxLayout()
        self.go = QPushButton("Process all")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.process)
        self.cancel = QPushButton("Cancel")
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(lambda: self.worker and self.worker.cancel())
        self.open_btn = QPushButton("Open output folder")
        self.open_btn.setObjectName("ghost")
        self.open_btn.clicked.connect(self.open_out)
        br.addWidget(self.go, 3)
        br.addWidget(self.cancel, 1)
        br.addWidget(self.open_btn, 1)
        lay.addLayout(br)
        fr = QHBoxLayout()
        self.c_notify = QCheckBox("Notify me when finished")
        self.c_notify.setChecked(True)
        self.c_openwhen = QCheckBox("Open folder when finished")
        fr.addWidget(self.c_notify)
        fr.addWidget(self.c_openwhen)
        lay.addLayout(fr)
        return f

    def _right(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(176)
        self.stack = QStackedWidget()
        pages = [("Voice", self.page_voice()), ("Background", self.page_background()),
                 ("Format & Quality", self.page_format()),
                 ("Effects & Colour", self.page_effects()), ("Music", self.page_music()),
                 ("Intro / Outro", self.page_intro()), ("Audio / Video tools", self.page_tools()),
                 ("Create video (AI)", self.page_make()),
                 ("AI Studio (online)", self.page_ai()),
                 ("Appearance", self.page_appearance())]
        for name, page in pages:
            self.nav.addItem(name)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        h.addWidget(self.nav)
        h.addWidget(self.stack, 1)
        return w

    # ---------------- pages
    def page_voice(self):
        f, l = card("Voice enhancement", "Cleans and boosts the speech in your main video.")
        self.c_noise = QCheckBox("Remove background noise (fan, hiss, room tone)")
        self.c_noise.setChecked(True)
        sw, self.s_noise = slider(5, 30, 12, lambda v: f"{v} dB")
        self.c_voice = QCheckBox("Boost voice - clarity, presence and warmth EQ")
        self.c_voice.setChecked(True)
        self.c_comp = QCheckBox("Even out volume (compressor)")
        self.c_comp.setChecked(True)
        self.c_norm = QCheckBox("Increase / normalize loudness")
        self.c_norm.setChecked(True)
        lw, self.s_lufs = slider(-24, -9, -14, lambda v: f"{v} LUFS")
        self.s_gain = QSpinBox()
        self.s_gain.setRange(-20, 20)
        self.s_gain.setSuffix(" dB")
        for x in (self.c_noise, row("Noise strength", sw), self.c_voice, self.c_comp, self.c_norm,
                  row("Target loudness", lw), row("Extra gain", self.s_gain)):
            l.addWidget(x)
        hint = QLabel("-14 LUFS is the YouTube / Instagram standard. Louder = closer to -9, quieter = -16.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        l.addWidget(hint)
        f2, l2 = card("Audio only mode")
        self.c_audio_only = QCheckBox("Fix audio only - keep the video untouched (very fast)")
        l2.addWidget(self.c_audio_only)
        n = QLabel("Ignores format, effects, intro/outro, speed and trim. Background music still works.")
        n.setObjectName("muted")
        n.setWordWrap(True)
        l2.addWidget(n)
        return make_page(f, f2)

    def page_background(self):
        f, l = card("Replace your background",
                    "AI cuts you out of the video and puts you in any scene - for example your university compound. "
                    "Works fully offline.")
        self.m_mode = combo(MAT_MODES)
        self.m_path = FilePick("Images and videos (*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.mkv *.webm);;All files (*)")
        self.m_color = "#00B140"
        self.m_btn = QPushButton("Pick colour (#00B140)")
        self.m_btn.clicked.connect(self.pick_mat_color)
        ew, self.m_edge = slider(0, 100, 20, lambda v: f"{v}%")
        self.m_light = QCheckBox("Match my brightness to the new scene (more natural)")
        self.m_light.setChecked(True)
        self.m_quality = combo(MAT_QUALITY)
        self.m_rows = {"path": row("Background file", self.m_path), "color": row("Colour", self.m_btn)}
        l.addWidget(row("Background", self.m_mode))
        l.addWidget(self.m_rows["path"])
        l.addWidget(self.m_rows["color"])
        l.addWidget(row("Edge sharpness", ew, "Higher = crisper outline around you. Lower = softer, more natural hair."))
        l.addWidget(self.m_light)
        l.addWidget(row("Speed / quality", self.m_quality))
        self.m_mode.currentIndexChanged.connect(self._mat_mode_changed)
        self._mat_mode_changed()
        f2, l2 = card("Tips for a clean result")
        t = QLabel("- Use Preview first: it renders only a few seconds so you can check the edges.\n"
                   "- Good, even light on your face and a background different from your clothes works best.\n"
                   "- Keep the camera still. Sit a little away from the wall.\n"
                   "- Use a photo of the same orientation (landscape photo for landscape video).\n"
                   "- This is the slowest feature: roughly 1-3x the video length on a typical laptop, faster on PCs "
                   "with more CPU cores.")
        t.setObjectName("muted")
        t.setWordWrap(True)
        l2.addWidget(t)
        return make_page(f, f2)

    def _mat_mode_changed(self):
        mode = self.m_mode.currentText()
        self.m_rows["path"].setVisible(mode in ("Image", "Video"))
        self.m_rows["color"].setVisible(mode == "Solid colour")

    def pick_mat_color(self):
        c = QColorDialog.getColor(QColor(self.m_color), self)
        if c.isValid():
            self.m_color = c.name().upper()
            self.m_btn.setText(f"Pick colour ({self.m_color})")

    def page_format(self):
        f, l = card("Shape & size")
        self.o_orient = combo(ORIENTATIONS)
        self.o_quality = combo(QUALITIES, "1080p (Full HD)")
        self.o_fit = combo(FIT_MODES)
        self.btn_col = QPushButton("Pick colour (black)")
        self.btn_col.clicked.connect(self.pick_color)
        self.bg_img = FilePick(IMAGE_FILTER)
        self.o_fps = combo(["24", "25", "30", "50", "60"], "30")
        for lab, w in (("Orientation", self.o_orient), ("Resolution", self.o_quality), ("When shape differs", self.o_fit),
                       ("Solid colour", self.btn_col), ("Background image", self.bg_img), ("Frame rate", self.o_fps)):
            l.addWidget(row(lab, w))
        f2, l2 = card("Compression", "Smaller files take less space; higher quality looks sharper.")
        self.o_comp = combo(COMPRESSION, "Balanced")
        self.o_codec = combo(CODECS)
        l2.addWidget(row("Quality / size", self.o_comp))
        l2.addWidget(row("Codec", self.o_codec))
        return make_page(f, f2)

    def page_effects(self):
        f, l = card("Colour grading")
        self.o_look = combo(COLOR_PRESETS)
        l.addWidget(row("Look", self.o_look))
        self.sl = {}
        for key, lab, lo, hi in (("brightness", "Brightness", -50, 50), ("contrast", "Contrast", -50, 50),
                                 ("saturation", "Saturation", -50, 50), ("gamma", "Gamma", -50, 50),
                                 ("warmth", "Warmth (cool - warm)", -50, 50)):
            w, s = slider(lo, hi, 0, lambda v: f"{v:+d}")
            self.sl[key] = s
            l.addWidget(row(lab, w))
        reset = QPushButton("Reset colour")
        reset.setObjectName("ghost")
        reset.clicked.connect(lambda: [s.setValue(0) for s in self.sl.values()] + [self.o_look.setCurrentIndex(0)])
        l.addWidget(reset, 0, Qt.AlignLeft)

        f2, l2 = card("Finishing")
        sw, self.s_sharp = slider(0, 100, 0, lambda v: f"{v}%")
        l2.addWidget(row("Sharpen", sw))
        self.c_vig = QCheckBox("Vignette (darker edges)")
        self.c_vdn = QCheckBox("Reduce video grain / noise")
        self.c_mirror = QCheckBox("Mirror (flip left-right)")
        for c in (self.c_vig, self.c_vdn, self.c_mirror):
            l2.addWidget(c)
        self.fade_in = spin(0, 5, 0)
        self.fade_out = spin(0, 5, 0)
        l2.addWidget(row("Fade in", self.fade_in))
        l2.addWidget(row("Fade out", self.fade_out))

        f3, l3 = card("Speed & trim")
        self.o_speed = combo(["0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x"], "1x")
        self.t_start = spin(0, 360000, 0, " s", 1)
        self.t_end = spin(0, 360000, 0, " s (0 = end)", 1)
        l3.addWidget(row("Speed", self.o_speed))
        l3.addWidget(row("Start at", self.t_start))
        l3.addWidget(row("End at", self.t_end))

        f4, l4 = card("Logo / watermark")
        self.wm = FilePick(IMAGE_FILTER)
        self.wm_pos = combo(WM_POSITIONS, "Bottom right")
        sw, self.wm_size = slider(5, 50, 15, lambda v: f"{v}%")
        ow, self.wm_op = slider(10, 100, 80, lambda v: f"{v}%")
        l4.addWidget(row("Image (PNG best)", self.wm))
        l4.addWidget(row("Position", self.wm_pos))
        l4.addWidget(row("Size", sw))
        l4.addWidget(row("Opacity", ow))
        return make_page(f, f2, f3, f4)

    def page_music(self):
        f, l = card("Background music", "Loops to fit your video and fades out at the end.")
        self.music = FilePick(AUDIO_FILTER)
        vw, self.m_vol = slider(0, 100, 20, lambda v: f"{v}%")
        self.c_duck = QCheckBox("Lower the music automatically while someone is speaking")
        self.c_duck.setChecked(True)
        self.m_fade = spin(0, 10, 2)
        l.addWidget(row("Music file", self.music))
        l.addWidget(row("Music volume", vw))
        l.addWidget(self.c_duck)
        l.addWidget(row("Fade out", self.m_fade))
        return make_page(f)

    def page_intro(self):
        f, l = card("Intro & outro", "Clips are resized to match your video and joined automatically.")
        self.intro = FilePick(VIDEO_FILTER)
        self.outro = FilePick(VIDEO_FILTER)
        l.addWidget(row("Intro video", self.intro))
        l.addWidget(row("Outro video", self.outro))
        n = QLabel("Voice enhancement and effects apply to the main video only. Preview skips intro/outro.")
        n.setObjectName("muted")
        n.setWordWrap(True)
        l.addWidget(n)
        return make_page(f)

    def page_tools(self):
        f, l = card("Separate audio & video", "These tools work on every video in the queue and are very fast.")
        self.x_fmt = combo(AUDIO_FORMATS)
        self.x_enh = QCheckBox("Apply voice enhancement to the extracted audio")
        l.addWidget(row("Audio format", self.x_fmt))
        l.addWidget(self.x_enh)
        for text, kind in (("Extract audio", "extract"), ("Save video without audio", "mute"),
                           ("Split into audio file + silent video", "split")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, k=kind: self.run_tool(k))
            l.addWidget(b)
        f2, l2 = card("Add or replace audio", "Put a new soundtrack or voice-over on the queued videos (video is not re-encoded).")
        self.a_file = FilePick(AUDIO_FILTER)
        self.a_mode = combo(["Replace original audio", "Mix with original audio"])
        vw, self.a_vol = slider(0, 200, 100, lambda v: f"{v}%")
        self.a_loop = QCheckBox("Loop the audio if it is shorter than the video")
        self.a_loop.setChecked(True)
        l2.addWidget(row("Audio file", self.a_file))
        l2.addWidget(row("Mode", self.a_mode))
        l2.addWidget(row("Volume", vw))
        l2.addWidget(self.a_loop)
        b = QPushButton("Add audio to queued videos")
        b.clicked.connect(lambda: self.run_tool("add"))
        l2.addWidget(b)
        return make_page(f, f2)

    def page_make(self):
        """Prompt in, finished video out. Planning/voice/media come online; rendering happens on this PC."""
        self.cloud = CloudClient()
        f, l = card("Create a finished video", "Describe the video. VoxCut gets a plan, narration, pictures/clips and "
                    "music online (needs your API key - set it in the AI Studio tab), then builds the MP4 here on your PC.")
        self.mk_prompt = QTextEdit()
        self.mk_prompt.setPlaceholderText("e.g. A five-part story about friendship with warm visuals")
        self.mk_prompt.setFixedHeight(90)
        self.mk_fmt = combo(list(MAKER_FORMATS), list(MAKER_FORMATS)[0])
        self.mk_narr = QCheckBox("AI narration")
        self.mk_narr.setChecked(True)
        self.mk_music = QCheckBox("Background music")
        self.mk_music.setChecked(True)
        self.mk_go = QPushButton("Create video")
        self.mk_go.clicked.connect(self.make_start)
        self.mk_cancel = QPushButton("Cancel")
        self.mk_cancel.setEnabled(False)
        self.mk_cancel.clicked.connect(lambda: self.mk_worker and self.mk_worker.cancel())
        self.mk_bar = QProgressBar()
        self.mk_bar.setRange(0, 100)
        self.mk_status = QLabel("")
        self.mk_status.setObjectName("muted")
        self.mk_status.setWordWrap(True)
        self.mk_play = QPushButton("Play result")
        self.mk_folder = QPushButton("Open folder")
        self.mk_play.hide()
        self.mk_folder.hide()
        self.mk_out = ""
        self.mk_worker = None
        self.mk_play.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.mk_out)))
        self.mk_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.mk_out))))
        l.addWidget(self.mk_prompt)
        l.addWidget(row("Format", self.mk_fmt))
        l.addWidget(self.mk_narr)
        l.addWidget(self.mk_music)
        l.addWidget(self.mk_go)
        l.addWidget(self.mk_cancel)
        l.addWidget(self.mk_bar)
        l.addWidget(self.mk_status)
        l.addWidget(self.mk_play)
        l.addWidget(self.mk_folder)
        return make_page(f)

    def make_start(self):
        prompt = self.mk_prompt.toPlainText().strip()
        if not prompt:
            return
        if not self.cloud.signed_in:
            QMessageBox.information(self, "VoxCut", "Paste your API key in the AI Studio tab first, then click Use key.")
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save video as", "VoxCut AI video.mp4", "MP4 video (*.mp4)")
        if not out:
            return
        self.mk_out = out
        self.mk_go.setEnabled(False)
        self.mk_cancel.setEnabled(True)
        self.mk_play.hide()
        self.mk_folder.hide()
        self.mk_bar.setValue(0)
        keep_awake(True)
        w = MakerWorker(self.cloud, prompt, out, self.mk_fmt.currentText(), self.mk_narr.isChecked(),
                        self.mk_music.isChecked())
        self.mk_worker = w
        w.status.connect(self.mk_status.setText)
        w.progress.connect(lambda p: self.mk_bar.setValue(int(p)))
        w.finished_ok.connect(self.make_done)
        w.failed.connect(self.make_failed)
        w.start()

    def _make_reset(self):
        keep_awake(False)
        self.mk_go.setEnabled(True)
        self.mk_cancel.setEnabled(False)

    def make_done(self, res):
        self._make_reset()
        self.mk_bar.setValue(100)
        msg = f"Done - {res['scenes']} scenes, {res['duration']:.0f}s. Saved to {res['path']}"
        if res.get("notes"):
            msg += "\n" + "\n".join(res["notes"])
        self.mk_status.setText(msg)
        self.mk_play.show()
        self.mk_folder.show()

    def make_failed(self, err):
        self._make_reset()
        self.mk_status.setText("Stopped: " + err)
        if err != "Cancelled":
            QMessageBox.warning(self, "VoxCut", err)

    def page_ai(self):
        """Online-only helpers (sign-in required). Rendering itself never leaves this PC."""
        self.cloud = getattr(self, "cloud", None) or CloudClient()
        f, l = card("API key", "Paste the API key from your QuoteTube web app. Needed for AI writing and "
                    "narration only - rendering stays offline. Treat it like a password.")
        self.ai_key = QLineEdit(self.qs.value("ai_key", ""))
        self.ai_key.setEchoMode(QLineEdit.Password)
        self.ai_key.setPlaceholderText("paste API key")
        self.ai_remember = QCheckBox("Remember on this PC")
        self.ai_remember.setChecked(bool(self.qs.value("ai_key", "")))
        self.ai_key.returnPressed.connect(self.ai_save_key)
        self.ai_btn = QPushButton("Use key")
        self.ai_btn.clicked.connect(self.ai_save_key)
        self.ai_status = QLabel("No key set")
        self.ai_status.setObjectName("muted")
        l.addWidget(row("API key", self.ai_key))
        l.addWidget(self.ai_remember)
        l.addWidget(self.ai_btn)
        l.addWidget(self.ai_status)
        if self.ai_key.text().strip():
            self.cloud.set_key(self.ai_key.text())
            self.ai_status.setText("Key loaded")

        f2, l2 = card("Ideas", "AI-written quotes for a topic.")
        self.ai_topic = QLineEdit()
        self.ai_topic.setPlaceholderText("e.g. friendship")
        self.ai_count = QSpinBox()
        self.ai_count.setRange(1, 20)
        self.ai_count.setValue(5)
        b = QPushButton("Get ideas")
        b.clicked.connect(self.ai_ideas)
        self.ai_out = QTextEdit()
        self.ai_out.setMinimumHeight(160)
        l2.addWidget(row("Topic", self.ai_topic))
        l2.addWidget(row("How many", self.ai_count))
        l2.addWidget(b)
        l2.addWidget(self.ai_out)

        f3, l3 = card("Narration", "Turn the text above into an MP3 voice-over, then add it with Audio / Video tools.")
        self.ai_voice = combo(["alloy", "echo", "fable", "onyx", "nova", "shimmer"], "alloy")
        b3 = QPushButton("Create narration MP3...")
        b3.clicked.connect(self.ai_tts)
        l3.addWidget(row("Voice", self.ai_voice))
        l3.addWidget(b3)
        return make_page(f, f2, f3)

    def _ai_call(self, fn):
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            return fn()
        except CloudError as e:
            QMessageBox.warning(self, "VoxCut online", str(e))
        finally:
            QApplication.restoreOverrideCursor()
        return None

    def ai_save_key(self):
        key = self.cloud.set_key(self.ai_key.text())
        if key and self.ai_remember.isChecked():
            self.qs.setValue("ai_key", key)
        else:
            self.qs.remove("ai_key")
        self.ai_status.setText("Key set (checked on first use)" if key else "No key set")

    def ai_ideas(self):
        topic = self.ai_topic.text().strip()
        if not topic:
            return
        res = self._ai_call(lambda: self.cloud.ideas(topic, int(self.ai_count.value())))
        if res is not None:
            import json
            self.ai_out.setPlainText(json.dumps(res, indent=2, ensure_ascii=False))

    def ai_tts(self):
        text = self.ai_out.toPlainText().strip()
        if not text:
            return
        mp3 = self._ai_call(lambda: self.cloud.tts(text, self.ai_voice.currentText()))
        if mp3:
            path, _ = QFileDialog.getSaveFileName(self, "Save narration", "narration.mp3", "MP3 (*.mp3)")
            if path:
                with open(path, "wb") as fh:
                    fh.write(mp3)

    def page_appearance(self):
        f, l = card("Theme", "Pick the look you like. Changes apply instantly and are remembered.")
        self.o_theme = combo(THEMES, self.qs.value("theme", "Midnight"))
        self.o_theme.currentIndexChanged.connect(lambda _: self.restyle(True))
        l.addWidget(row("Colour theme", self.o_theme))
        f2, l2 = card("Accent colours", "Used for buttons, sliders and highlights.")
        sw = QHBoxLayout()
        for name, col in ACCENT_PRESETS.items():
            b = QPushButton()
            b.setFixedSize(30, 30)
            b.setToolTip(name)
            b.setStyleSheet(f"background:{col}; border-radius:15px; border:2px solid transparent;")
            b.clicked.connect(lambda _=False, c=col: self.set_accent(c))
            sw.addWidget(b)
        sw.addStretch(1)
        wsw = QWidget()
        wsw.setLayout(sw)
        l2.addWidget(wsw)
        r = QHBoxLayout()
        b1 = QPushButton("Custom accent...")
        b1.clicked.connect(lambda: self.custom_color("accent"))
        b2 = QPushButton("Custom highlight...")
        b2.clicked.connect(lambda: self.custom_color("accent2"))
        b3 = QPushButton("Reset to theme")
        b3.setObjectName("ghost")
        b3.clicked.connect(self.reset_accent)
        for b in (b1, b2, b3):
            r.addWidget(b)
        wr = QWidget()
        wr.setLayout(r)
        l2.addWidget(wr)
        f3, l3 = card("Text size")
        self.o_size = combo(["Compact", "Normal", "Large"], self.qs.value("size", "Normal"))
        self.o_size.currentIndexChanged.connect(lambda _: self.restyle(True))
        l3.addWidget(row("Interface size", self.o_size))
        return make_page(f, f2, f3)

    # ---------------- theme
    def restyle(self, save=False):
        pt = {"Compact": 9, "Normal": 10, "Large": 12}[self.o_size.currentText()]
        apply_theme(self.app, self.o_theme.currentText(), self.qs.value("accent", ""), self.qs.value("accent2", ""), pt)
        if save:
            self.qs.setValue("theme", self.o_theme.currentText())
            self.qs.setValue("size", self.o_size.currentText())

    def set_accent(self, col):
        self.qs.setValue("accent", col)
        self.qs.setValue("accent2", "")
        self.restyle()

    def custom_color(self, key):
        c = QColorDialog.getColor(QColor(self.qs.value(key, "#7C6CFF") or "#7C6CFF"), self)
        if c.isValid():
            self.qs.setValue(key, c.name())
            self.restyle()

    def reset_accent(self):
        self.qs.setValue("accent", "")
        self.qs.setValue("accent2", "")
        self.restyle()

    # ---------------- presets & settings
    def apply_preset(self):
        p = PRESETS.get(self.preset.currentText())
        if not p:
            return
        if "orient" in p:
            self.o_orient.setCurrentText(p["orient"])
        if "quality" in p:
            self.o_quality.setCurrentText(p["quality"])
        if "comp" in p:
            self.o_comp.setCurrentText(p["comp"])
        if "codec" in p:
            self.o_codec.setCurrentText(p["codec"])
        if "lufs" in p:
            self.s_lufs.setValue(p["lufs"])
        if "strength" in p:
            self.s_noise.setValue(p["strength"])
        self.c_audio_only.setChecked(bool(p.get("audio_only", False)))

    def settings(self) -> Settings:
        return Settings(
            noise_removal=self.c_noise.isChecked(), noise_strength=self.s_noise.value(),
            voice_boost=self.c_voice.isChecked(), compressor=self.c_comp.isChecked(),
            normalize=self.c_norm.isChecked(), target_lufs=float(self.s_lufs.value()),
            extra_gain_db=float(self.s_gain.value()), audio_only_enhance=self.c_audio_only.isChecked(),
            orientation=self.o_orient.currentText(), quality=self.o_quality.currentText(),
            fit_mode=self.o_fit.currentText(), bg_color=self.bg_color, bg_image=self.bg_img.value(),
            fps=int(self.o_fps.currentText()), compression=self.o_comp.currentText(), codec=self.o_codec.currentText(),
            color_preset=self.o_look.currentText(), brightness=self.sl["brightness"].value(),
            contrast=self.sl["contrast"].value(), saturation=self.sl["saturation"].value(),
            gamma=self.sl["gamma"].value(), warmth=self.sl["warmth"].value(), sharpen=self.s_sharp.value(),
            vignette=self.c_vig.isChecked(), video_denoise=self.c_vdn.isChecked(), mirror=self.c_mirror.isChecked(),
            fade_in=self.fade_in.value(), fade_out=self.fade_out.value(),
            speed=float(self.o_speed.currentText().rstrip("x")), trim_start=self.t_start.value(),
            trim_end=self.t_end.value(), watermark=self.wm.value(), wm_position=self.wm_pos.currentText(),
            wm_size=self.wm_size.value(), wm_opacity=self.wm_op.value(), music=self.music.value(),
            music_volume=self.m_vol.value(), music_duck=self.c_duck.isChecked(), music_fade_out=self.m_fade.value(),
            intro=self.intro.value(), outro=self.outro.value(), mat_mode=self.m_mode.currentText(),
            mat_path=self.m_path.value(), mat_color=self.m_color, mat_edge=self.m_edge.value(),
            mat_light=self.m_light.isChecked(), mat_quality=self.m_quality.currentText())

    def validate(self, s: Settings):
        for label, path in (("Music", s.music), ("Logo", s.watermark), ("Background image", s.bg_image),
                            ("Intro", s.intro), ("Outro", s.outro)):
            if path and not os.path.isfile(path):
                return f"{label} file not found:\n{path}"
        if s.mat_mode != "Off":
            if s.audio_only_enhance:
                return "Turn off 'Audio only mode' (Voice tab) to replace the background."
            ok, why = matting_available()
            if not ok:
                return why
            if s.mat_mode in ("Image", "Video") and not (s.mat_path and os.path.isfile(s.mat_path)):
                return "Choose a background image or video file on the Background tab."
        if s.fit_mode == "Image background" and not s.bg_image:
            return "Choose a background image, or pick another 'When shape differs' option."
        if s.trim_end and s.trim_end <= s.trim_start:
            return "'End at' must be later than 'Start at' (or 0 for the end)."
        return None

    # ---------------- queue
    def files(self):
        return [self.listw.item(i).data(Qt.UserRole) for i in range(self.listw.count())]

    def add_paths(self, paths):
        have = set(self.files())
        for p in paths:
            if os.path.isfile(p) and p not in have:
                it = QListWidgetItem(os.path.basename(p))
                it.setData(Qt.UserRole, p)
                it.setToolTip(p)
                self.listw.addItem(it)

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add videos", "", VIDEO_FILTER)
        self.add_paths(files)

    def remove_sel(self):
        for it in self.listw.selectedItems():
            self.listw.takeItem(self.listw.row(it))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls()])

    def pick_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder")
        if d:
            self.outdir.setText(d)

    def pick_color(self):
        c = QColorDialog.getColor(parent=self)
        if c.isValid():
            self.bg_color = c.name().replace("#", "0x")
            self.btn_col.setText(f"Colour {c.name()}")

    def out_path(self, src, suffix, ext="mp4"):
        d = self.outdir.text().strip() or os.path.dirname(src)
        os.makedirs(d, exist_ok=True)
        self.last_out_dir = d
        return os.path.join(d, f"{os.path.splitext(os.path.basename(src))[0]}{suffix}.{ext}")

    def open_out(self):
        d = self.last_out_dir or self.outdir.text().strip() or (os.path.dirname(self.files()[0]) if self.files() else "")
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    # ---------------- running work
    def need_files(self):
        if not self.files():
            QMessageBox.information(self, "VoxCut", "Add at least one video first.")
            return False
        return True

    def begin(self, tasks, title, after=None):
        self.results, self.after, self.total = [], after, len(tasks)
        self.log.clear()
        self.log.append(title)
        self.bar.setValue(0)
        self.status.setText(f"{title}...")
        for b in (self.go, self.btn_preview):
            b.setEnabled(False)
        self.cancel.setEnabled(True)
        self.qs.setValue("outdir", self.outdir.text().strip())
        keep_awake(True)
        self.worker = Worker(tasks)
        self.worker.progress.connect(lambda i, p: (self.bar.setValue(int((i + p / 100) / self.total * 100)),
                                                   self.status.setText(f"{title}: item {i + 1} of {self.total} - {int(p)}%")))
        self.worker.item_done.connect(self.on_item)
        self.worker.all_done.connect(self.on_all_done)
        self.worker.start()

    def on_item(self, i, msg, out):
        name = self.worker.tasks[i][0]
        ok = msg == "OK"
        self.results.append((ok, out))
        self.log.append(f"[{i + 1}/{self.total}] {'Done' if ok else 'FAILED'}: {name}" + ("" if ok else f"\n{msg}"))

    def on_all_done(self):
        keep_awake(False)
        self.go.setEnabled(True)
        self.btn_preview.setEnabled(True)
        self.cancel.setEnabled(False)
        ok = sum(1 for r in self.results if r[0])
        bad = len(self.results) - ok
        cancelled = self.worker.stop
        msg = ("Cancelled." if cancelled else f"Finished: {ok} done" + (f", {bad} failed" if bad else ""))
        self.bar.setValue(100 if ok and not bad and not cancelled else self.bar.value())
        self.status.setText(msg)
        self.log.append(msg)
        if self.after and ok and not cancelled:
            self.after([r[1] for r in self.results if r[0]])
            return
        if self.c_notify.isChecked() and not cancelled:
            QApplication.alert(self, 0)
            if self.tray:
                self.tray.showMessage("VoxCut", msg, QSystemTrayIcon.Information, 6000)
        if self.c_openwhen.isChecked() and ok and not cancelled:
            self.open_out()

    def _restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def process(self):
        if not self.need_files():
            return
        s = self.settings()
        err = self.validate(s)
        if err:
            QMessageBox.warning(self, "VoxCut", err)
            return
        tasks = []
        for f in self.files():
            out = self.out_path(f, "_enhanced")
            tasks.append((os.path.basename(f), lambda f=f, out=out: plan_enhance(f, out, s)))
        self.begin(tasks, "Processing")

    def preview(self):
        if not self.need_files():
            return
        s = self.settings()
        err = self.validate(s)
        if err:
            QMessageBox.warning(self, "VoxCut", err)
            return
        sel = self.listw.selectedItems()
        f = sel[0].data(Qt.UserRole) if sel else self.files()[0]
        if self.pv_dialog is not None:      # release the previous preview file (Windows locks open files)
            self.pv_dialog.close()
            self.pv_dialog = None
        self.pv_n += 1
        out = os.path.join(tempfile.gettempdir(), f"voxcut_preview_{os.getpid()}_{self.pv_n}.mp4")
        ps, pl = self.p_start.value(), self.p_len.value()
        self.begin([(os.path.basename(f), lambda: plan_enhance(f, out, s, preview=(ps, pl)))],
                   "Rendering preview", after=self._show_preview)

    def _show_preview(self, outs):
        self.pv_dialog = PreviewDialog(outs[0], self)
        self.pv_dialog.show()

    def run_tool(self, kind):
        if not self.need_files():
            return
        s = self.settings()
        tasks = []
        if kind == "add":
            audio = self.a_file.value()
            if not audio or not os.path.isfile(audio):
                QMessageBox.information(self, "VoxCut", "Choose an audio file first.")
                return
        for f in self.files():
            base = os.path.basename(f)
            if kind in ("extract", "split"):
                ext, _ = AUDIO_FORMATS[self.x_fmt.currentText()]
                out = self.out_path(f, "_audio", ext)
                tasks.append((f"{base} -> audio", lambda f=f, out=out: (*plan_extract_audio(
                    f, out, self.x_fmt.currentText(), self.x_enh.isChecked(), s), out)))
            if kind in ("mute", "split"):
                out = self.out_path(f, "_video_only")
                tasks.append((f"{base} -> video only", lambda f=f, out=out: (*plan_remove_audio(f, out), out)))
            if kind == "add":
                out = self.out_path(f, "_newaudio")
                mode = "mix" if self.a_mode.currentIndex() == 1 else "replace"
                tasks.append((f"{base} + audio", lambda f=f, out=out, mode=mode: (*plan_add_audio(
                    f, audio, out, mode, self.a_vol.value(), self.a_loop.isChecked()), out)))
        self.begin(tasks, "Working")

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        keep_awake(False)
        if self.pv_dialog is not None:
            self.pv_dialog.close()
        import glob
        for p in glob.glob(os.path.join(tempfile.gettempdir(), f"voxcut_preview_{os.getpid()}_*.mp4")):
            try:
                os.remove(p)
            except OSError:
                pass
        super().closeEvent(e)


def main():
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VoxCut.Studio")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("VoxCut")
    app.setWindowIcon(QIcon(asset_path("icon.png")))
    w = Main(app)
    w.add_paths(sys.argv[1:])
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
