"""'Lecture recorder' tab: record any screen/window/area (+ webcam + mic) with no time limit, draw over other windows,
use the whiteboard, drop chapter markers, then clean the voice and send the result to the editor queue."""
from __future__ import annotations

import array
import datetime
import os
import tempfile

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QWidget)

from . import recorder as R
from .engine import Job, media_info
from .overlay import Hotkeys, RecorderBar, RegionSelector, ScreenOverlay
from .whiteboard import WhiteboardWindow

SOURCES = ["Entire screen", "One window", "Part of the screen (drag a box)", "Whiteboard window"]


# --------------------------------------------------------------------------- microphone level meter
class MicMeter(QObject):
    level = Signal(int)

    def __init__(self):
        super().__init__()
        self.src = None
        self.io = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)

    def start(self, hint: str = "") -> bool:
        self.stop()
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
            hint_l = (hint or "").lower()
            dev = None
            for d in QMediaDevices.audioInputs():
                n = d.description().lower()
                if hint_l and (hint_l in n or n in hint_l):
                    dev = d
                    break
            dev = dev or QMediaDevices.defaultAudioInput()
            if dev.isNull():
                return False
            fmt = QAudioFormat()
            fmt.setSampleRate(16000)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.Int16)
            self.src = QAudioSource(dev, fmt)
            self.io = self.src.start()
            self.timer.start(60)
            return True
        except Exception:  # noqa: BLE001
            return False

    def stop(self):
        self.timer.stop()
        if self.src is not None:
            try:
                self.src.stop()
            except Exception:  # noqa: BLE001
                pass
        self.src = self.io = None
        self.level.emit(0)

    def _poll(self):
        if not self.io:
            return
        data = bytes(self.io.readAll())
        if len(data) < 2:
            return
        a = array.array("h")
        a.frombytes(data[: len(data) // 2 * 2])
        peak = max(abs(x) for x in a) / 32768.0
        self.level.emit(min(100, int(peak * 140)))


# --------------------------------------------------------------------------- background finishing
class FinishWorker(QThread):
    status = Signal(str)
    progress = Signal(float)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, rec: R.Recorder, clean: bool):
        super().__init__()
        self.rec, self.clean = rec, clean

    def run(self):
        try:
            self.status.emit("Saving your recording...")
            res = self.rec.stop()
            if self.clean:
                self.status.emit("Cleaning the voice (noise removal, levelling)...")
                src = res["path"]
                tmp = os.path.splitext(src)[0] + ".clean.tmp.mp4"
                dur = max(media_info(src)["duration"], 1.0)
                Job(R.clean_voice_cmd(src, tmp), dur).run(self.progress.emit)
                os.replace(tmp, src)
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


# --------------------------------------------------------------------------- the tab
class LecturePanel(QObject):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.devices = {"video": [], "audio": []}
        self.monitors = []
        self.windows = []
        self.region = None
        self.rec: R.Recorder | None = None
        self.overlay = self.bar = self.hot = self.selector = self.countdown = None
        self.whiteboard: WhiteboardWindow | None = None
        self.worker = None
        self.result_path = ""
        self.meter = MicMeter()
        self.page = self._build()
        QTimer.singleShot(400, self.scan)

    # ---------------- UI
    def _build(self):
        from . import gui as G
        f1, l1 = G.card("What to record", "Records other programs too (PowerPoint, browser, PDF, anything on screen). "
                        "There is no time limit.")
        self.src = G.combo(SOURCES, SOURCES[0])
        self.src2 = G.combo([])
        self.area_btn = QPushButton("Choose area...")
        self.area_btn.clicked.connect(self.choose_area)
        self.area_lbl = QLabel("")
        self.area_lbl.setObjectName("muted")
        self.refresh_btn = QPushButton("Refresh screens and windows")
        self.refresh_btn.clicked.connect(self.scan)
        self.src.currentIndexChanged.connect(self._src_changed)
        l1.addWidget(G.row("Source", self.src))
        l1.addWidget(G.row("Which one", self.src2))
        l1.addWidget(self.area_btn)
        l1.addWidget(self.area_lbl)
        l1.addWidget(self.refresh_btn)

        f2, l2 = G.card("Microphone, computer sound and camera",
                        "Computer sound needs a device such as 'Stereo Mix' (enable it in Windows Sound settings).")
        self.mic = G.combo(["None"])
        self.meter_bar = QProgressBar()
        self.meter_bar.setRange(0, 100)
        self.meter_bar.setTextVisible(False)
        self.meter_bar.setFixedHeight(10)
        self.mic.currentTextChanged.connect(self._mic_changed)
        self.meter.level.connect(self.meter_bar.setValue)
        self.sysa = G.combo(["None"])
        self.cam = G.combo(["None"])
        self.cam_pos = G.combo(R.CAM_POSITIONS, R.CAM_POSITIONS[0])
        self.cam_size = G.combo(list(R.CAM_SIZES), "Medium")
        l2.addWidget(G.row("Microphone", self.mic))
        l2.addWidget(self.meter_bar)
        l2.addWidget(G.row("Computer sound", self.sysa))
        l2.addWidget(G.row("Webcam", self.cam))
        l2.addWidget(G.row("Webcam position", self.cam_pos))
        l2.addWidget(G.row("Webcam size", self.cam_size))

        f3, l3 = G.card("Options")
        self.fps = G.combo(["30 fps (smooth)", "15 fps (light, good for slides)"], "30 fps (smooth)")
        self.quality = G.combo(["Light on the computer (best for long lectures)", "Higher quality"],
                               "Light on the computer (best for long lectures)")
        self.countdown_cb = QCheckBox("3-second countdown before recording")
        self.countdown_cb.setChecked(True)
        self.minimize_cb = QCheckBox("Minimise this window while recording")
        self.minimize_cb.setChecked(True)
        self.clean_cb = QCheckBox("Clean my voice after recording (noise removal + even volume)")
        self.clean_cb.setChecked(True)
        self.ring_cb = QCheckBox("Highlight the mouse cursor with a ring")
        self.ring_cb.setChecked(True)
        l3.addWidget(G.row("Smoothness", self.fps))
        l3.addWidget(G.row("Quality", self.quality))
        for c in (self.countdown_cb, self.minimize_cb, self.clean_cb, self.ring_cb):
            l3.addWidget(c)

        f4, l4 = G.card("Save to")
        default = os.path.join(os.path.expanduser("~"), "Videos")
        if not os.path.isdir(default):
            default = os.path.join(os.path.expanduser("~"), "Desktop")
        self.folder = QLineEdit(self.main.qs.value("lecture_folder", default))
        b = QPushButton("Browse...")
        b.clicked.connect(self.browse)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.folder, 1)
        h.addWidget(b)
        self.name = QLineEdit("Lecture")
        l4.addWidget(G.row("Folder", w))
        l4.addWidget(G.row("Name", self.name))

        f5, l5 = G.card("Record", "While recording, a small toolbar floats on top (it is NOT in the video). Hotkeys: "
                        "Ctrl+Alt+P pause/resume, S stop, D draw on/off, C clear drawing, W whiteboard, M marker.")
        self.start_btn = QPushButton("Start recording")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start)
        self.board_btn = QPushButton("Open whiteboard")
        self.board_btn.clicked.connect(self.open_board)
        self.test_btn = QPushButton("Test my setup (5 seconds)")
        self.test_btn.clicked.connect(self.test_setup)
        self.recover_btn = QPushButton("Recover a recording after a crash...")
        self.recover_btn.clicked.connect(self.recover)
        self.bar = None
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 100)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.play_btn = QPushButton("Play")
        self.open_btn = QPushButton("Open folder")
        self.queue_btn = QPushButton("Send to editor queue (trim, effects, music...)")
        for b2 in (self.play_btn, self.open_btn, self.queue_btn):
            b2.hide()
        self.play_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.result_path)))
        self.open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.result_path))))
        self.queue_btn.clicked.connect(self.to_queue)
        for wdg in (self.start_btn, self.board_btn, self.test_btn, self.recover_btn, self.pbar, self.status,
                    self.play_btn, self.open_btn, self.queue_btn):
            l5.addWidget(wdg)
        self._src_changed()
        return G.make_page(f5, f1, f2, f3, f4)

    # ---------------- devices / sources
    def scan(self):
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.devices = R.list_devices()
            self.monitors = R.list_monitors()
            self.windows = R.list_windows(exclude_pids=(os.getpid(),))
        finally:
            QGuiApplication.restoreOverrideCursor()
        for cb, kind, keep in ((self.mic, "audio", True), (self.sysa, "audio", True), (self.cam, "video", True)):
            cur = cb.currentText()
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(["None"] + self.devices[kind])
            if cur in [cb.itemText(i) for i in range(cb.count())]:
                cb.setCurrentText(cur)
            elif cb is self.mic and self.devices["audio"]:
                cb.setCurrentIndex(1)
            elif cb is self.cam:
                cb.setCurrentIndex(0)
            cb.blockSignals(False)
        self._src_changed()
        self._mic_changed(self.mic.currentText())
        if not R.IS_WIN:
            self.status.setText("Screen and device capture work on Windows. (This build is not running on Windows.)")

    def _src_changed(self, *_):
        i = self.src.currentIndex()
        self.src2.clear()
        self.area_btn.setVisible(i == 2)
        self.area_lbl.setVisible(i == 2)
        self.src2.parentWidget().setVisible(i in (0, 1))
        if i == 0:
            names = [f"Screen {n + 1}{' (main)' if m['primary'] else ''} - {m['w']}x{m['h']}"
                     for n, m in enumerate(self.monitors)]
            self.src2.addItems(names + (["All screens together"] if len(self.monitors) > 1 else []) or ["Main screen"])
        elif i == 1:
            self.src2.addItems([w["title"][:70] for w in self.windows] or ["(no windows found - click Refresh)"])

    def _mic_changed(self, name):
        if name and name != "None":
            self.meter.start(name)
        else:
            self.meter.stop()

    def choose_area(self):
        self.selector = RegionSelector()
        self.selector.selected.connect(self._area_chosen)
        self.main.hide() if self.minimize_cb.isChecked() else None
        self.selector.cancelled.connect(self.main.show)
        self.selector.start()

    def _area_chosen(self, rect):
        self.region = rect
        self.area_lbl.setText(f"Area: {rect[2]} x {rect[3]} at ({rect[0]}, {rect[1]})")
        self.main.show()

    def browse(self):
        d = QFileDialog.getExistingDirectory(self.main, "Save recordings in", self.folder.text())
        if d:
            self.folder.setText(d)

    # ---------------- board
    def open_board(self):
        if self.whiteboard is None:
            self.whiteboard = WhiteboardWindow()
        self.whiteboard.show()
        self.whiteboard.raise_()
        self.whiteboard.activateWindow()

    # ---------------- building the configuration
    def _target_region(self):
        """-> (region, screen_for_overlay) or raises ValueError with a friendly message."""
        i = self.src.currentIndex()
        screens = QGuiApplication.screens()
        if i == 0:
            if not self.monitors:
                return None, QGuiApplication.primaryScreen()
            n = self.src2.currentIndex()
            if n >= len(self.monitors):                       # all screens together
                return None, QGuiApplication.primaryScreen()
            m = self.monitors[n]
            return (m["x"], m["y"], m["w"], m["h"]), self._screen_named(m["name"])
        if i == 1:
            n = self.src2.currentIndex()
            if not self.windows or n >= len(self.windows):
                raise ValueError("No window selected. Click 'Refresh screens and windows' and pick one.")
            w = self.windows[n]
            rc = R.window_rect(w["hwnd"]) or (w["x"], w["y"], w["w"], w["h"])
            return rc, self._screen_at(rc)
        if i == 2:
            if not self.region:
                raise ValueError("Click 'Choose area...' and drag a box first.")
            return self.region, self._screen_at(self.region)
        self.open_board()
        QGuiApplication.processEvents()
        rc = R.window_rect(int(self.whiteboard.winId()))
        if rc is None:
            return None, QGuiApplication.primaryScreen()
        return rc, self._screen_at(rc)

    def _screen_named(self, name):
        for s in QGuiApplication.screens():
            if s.name() == name:
                return s
        return QGuiApplication.primaryScreen()

    def _screen_at(self, rc):
        cx, cy = rc[0] + rc[2] // 2, rc[1] + rc[3] // 2
        for m in self.monitors:
            if m["x"] <= cx < m["x"] + m["w"] and m["y"] <= cy < m["y"] + m["h"]:
                return self._screen_named(m["name"])
        return QGuiApplication.primaryScreen()

    def _cfg(self, out_path):
        region, screen = self._target_region()
        pick = lambda cb: None if cb.currentText() in ("None", "") else cb.currentText()  # noqa: E731
        cfg = R.RecordConfig(
            out_path=out_path, region=region, fps=30 if self.fps.currentIndex() == 0 else 15,
            quality="light" if self.quality.currentIndex() == 0 else "balanced",
            mic=pick(self.mic), sys_audio=pick(self.sysa), camera=pick(self.cam),
            cam_pos=self.cam_pos.currentText(), cam_size=self.cam_size.currentText())
        return cfg, screen

    # ---------------- recording
    def start(self):
        if self.rec is not None:
            return
        folder = self.folder.text().strip()
        try:
            os.makedirs(folder, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M")
            safe = "".join(c for c in (self.name.text().strip() or "Lecture") if c not in '\\/:*?"<>|')
            out = os.path.join(folder, f"{safe} {stamp}.mp4")
            cfg, screen = self._cfg(out)
        except (ValueError, OSError) as e:
            QMessageBox.information(self.main, "Ideawood Studio", str(e))
            return
        if not cfg.mic and not QMessageBox.question(
                self.main, "No microphone", "No microphone is selected - the video will have no voice. Continue?"
        ) == QMessageBox.Yes:
            return
        self.main.qs.setValue("lecture_folder", folder)
        self.meter.stop()
        self.rec = R.Recorder(cfg)
        self._screen = screen
        self.result_path = ""
        for b in (self.play_btn, self.open_btn, self.queue_btn):
            b.hide()
        self.start_btn.setEnabled(False)
        if self.minimize_cb.isChecked():
            self.main.showMinimized()
            if self.whiteboard and self.src.currentIndex() != 3:
                self.whiteboard.hide()
        if self.countdown_cb.isChecked():
            self._run_countdown(3, self._begin)
        else:
            self._begin()

    def _run_countdown(self, n, then):
        from PySide6.QtWidgets import QLabel as L
        w = L(str(n))
        w.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        w.setAttribute(Qt.WA_TranslucentBackground, True)
        w.setAlignment(Qt.AlignCenter)
        w.setStyleSheet("color:white;font-size:140px;font-weight:800;background:rgba(0,0,0,150);border-radius:30px;")
        w.resize(240, 240)
        g = self._screen.geometry()
        w.move(g.center().x() - 120, g.center().y() - 120)
        w.show()
        R.exclude_from_capture(w)
        self.countdown = w
        left = [n]

        def tick():
            left[0] -= 1
            if left[0] <= 0:
                t.stop(); w.close(); self.countdown = None; then()
            else:
                w.setText(str(left[0]))
        t = QTimer(self)
        t.timeout.connect(tick)
        t.start(1000)
        self._cd_timer = t

    def _begin(self):
        try:
            self.rec.start()
        except R.RecorderError as e:
            self._abort(str(e))
            return
        self.overlay = ScreenOverlay(self._screen)
        self.overlay.cursor_ring = self.ring_cb.isChecked()
        self.overlay.set_tool("none")
        self.bar = RecorderBar()
        self.bar.place_on(self._screen)
        self.bar.pause_toggled.connect(self.toggle_pause)
        self.bar.stop_clicked.connect(self.stop)
        self.bar.marker_clicked.connect(self.add_marker)
        self.bar.tool_selected.connect(self.overlay.set_tool)
        self.bar.color_selected.connect(self._color)
        self.bar.undo_clicked.connect(self.overlay.undo)
        self.bar.clear_clicked.connect(self.overlay.clear)
        self.bar.board_clicked.connect(self.open_board)
        self.bar.show()
        self.hot = Hotkeys()
        for hid, key, fn in ((1, "P", self.toggle_pause), (2, "S", self.stop), (3, "D", self.toggle_draw),
                             (4, "C", self.overlay.clear), (5, "W", self.open_board), (6, "M", self.add_marker)):
            self.hot.register(hid, "ctrl+alt", key, fn)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick)
        self.clock.start(250)
        from .gui import keep_awake
        keep_awake(True)
        self.status.setText("Recording... use the floating toolbar or hotkeys.")

    def _color(self, c):
        self.overlay.set_color(c)
        if self.overlay.tool in ("none", "laser", "spotlight", "eraser"):
            self.bar.select_tool("pen")
            self.overlay.set_tool("pen")

    def _tick(self):
        if not self.rec:
            return
        err = self.rec.alive_error()
        if err:
            self.stop(error=err)
            return
        self.bar.set_time(R.fmt_time(self.rec.elapsed()), self.rec.state == "paused")

    def toggle_pause(self):
        if not self.rec:
            return
        if self.rec.state == "recording":
            self.rec.pause()
        elif self.rec.state == "paused":
            self.rec.resume()
        self._tick()

    def toggle_draw(self):
        if self.overlay.tool == "none":
            self.bar.select_tool("pen"); self.overlay.set_tool("pen")
        else:
            self.bar.select_tool("none"); self.overlay.set_tool("none")

    def add_marker(self):
        if self.rec:
            t = self.rec.mark()
            self.status.setText(f"Marker added at {R.fmt_time(t)} (saved as chapters when you stop).")

    def shutdown(self):
        """App is closing: never lose a lecture - stop and save any recording in progress."""
        if self.worker and self.worker.isRunning():
            self.worker.wait(120000)
        if self.rec is not None and self.rec.state in ("recording", "paused"):
            try:
                self._teardown_tools()
                self.rec.stop()
            except Exception:  # noqa: BLE001
                pass
            self.rec = None
        self.meter.stop()

    def _teardown_tools(self):
        if getattr(self, "clock", None):
            self.clock.stop()
        if self.hot:
            self.hot.unregister_all()
            self.hot = None
        for w in (self.bar, self.overlay, self.countdown):
            if w:
                w.close()
        self.bar = self.overlay = self.countdown = None
        from .gui import keep_awake
        keep_awake(False)

    def _abort(self, msg):
        self._teardown_tools()
        self.rec = None
        self.start_btn.setEnabled(True)
        self.main.showNormal()
        self.status.setText("Could not start: " + msg)
        QMessageBox.warning(self.main, "Ideawood Studio", "Could not start recording.\n\n" + msg)
        self._mic_changed(self.mic.currentText())

    def stop(self, error: str | None = None):
        if not self.rec or self.worker:
            return
        self._teardown_tools()
        self.main.showNormal()
        self.main.raise_()
        self.pbar.setValue(0)
        self.worker = FinishWorker(self.rec, self.clean_cb.isChecked())
        self.worker.status.connect(self.status.setText)
        self.worker.progress.connect(lambda p: self.pbar.setValue(int(p)))
        self.worker.done.connect(lambda r: self._finished(r, error))
        self.worker.failed.connect(self._finish_failed)
        self.worker.start()

    def _finished(self, res, error):
        self.worker = None
        self.rec = None
        self.start_btn.setEnabled(True)
        self.pbar.setValue(100)
        self.result_path = res["path"]
        i = media_info(res["path"])
        msg = f"Saved: {res['path']}  ({R.fmt_time(i['duration'])})"
        if res.get("chapters"):
            msg += f"\nChapters for YouTube: {res['chapters']}"
        if error:
            msg += f"\nRecording stopped early: {error}"
        self.status.setText(msg)
        for b in (self.play_btn, self.open_btn, self.queue_btn):
            b.show()
        self._mic_changed(self.mic.currentText())

    def _finish_failed(self, err):
        self.worker = None
        self.rec = None
        self.start_btn.setEnabled(True)
        self.status.setText("Could not save: " + err)
        QMessageBox.warning(self.main, "Ideawood Studio", err)

    # ---------------- test / recover / queue
    def test_setup(self):
        if self.rec is not None:
            return
        try:
            out = os.path.join(tempfile.gettempdir(), "ideawood_test_setup.mp4")
            cfg, _ = self._cfg(out)
        except ValueError as e:
            QMessageBox.information(self.main, "Ideawood Studio", str(e))
            return
        self.meter.stop()
        self.test_btn.setEnabled(False)
        self.status.setText("Testing for 5 seconds - speak normally...")
        rec = R.Recorder(cfg)
        try:
            rec.start()
        except R.RecorderError as e:
            self.test_btn.setEnabled(True)
            self.status.setText("Test failed: " + str(e))
            QMessageBox.warning(self.main, "Ideawood Studio", str(e))
            return

        def finish():
            try:
                res = rec.stop()
                msg = R.describe_audio(R.analyze_audio(res["path"]))
                self.status.setText("Test done. " + msg + " Playing the test video...")
                QDesktopServices.openUrl(QUrl.fromLocalFile(res["path"]))
            except Exception as e:  # noqa: BLE001
                self.status.setText("Test failed: " + str(e))
            self.test_btn.setEnabled(True)
            self._mic_changed(self.mic.currentText())
        QTimer.singleShot(5000, finish)

    def recover(self):
        d = QFileDialog.getExistingDirectory(self.main, "Choose the '.parts' folder of the interrupted recording",
                                             self.folder.text())
        if not d:
            return
        try:
            res = R.recover_parts(d)
        except R.RecorderError as e:
            QMessageBox.warning(self.main, "Ideawood Studio", str(e))
            return
        self.result_path = res["path"]
        self.status.setText(f"Recovered: {res['path']}")
        for b in (self.play_btn, self.open_btn, self.queue_btn):
            b.show()

    def to_queue(self):
        if self.result_path and os.path.isfile(self.result_path):
            self.main.add_paths([self.result_path])
            self.status.setText("Added to the queue on the left - choose Voice / Effects / Music settings and press Process all.")
