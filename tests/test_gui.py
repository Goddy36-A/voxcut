import os, subprocess, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer
from voxcut.gui import Main, PreviewDialog
from voxcut.engine import media_info
from voxcut.paths import find_tool

app = QApplication([])
d = tempfile.mkdtemp()
FF = find_tool("ffmpeg")
src = os.path.join(d, "clip.mp4")
subprocess.run([FF, "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=3", "-f", "lavfi",
                "-i", "sine=f=300:duration=3", "-shortest", "-pix_fmt", "yuv420p", src], check=True, capture_output=True)
music = os.path.join(d, "m.mp3")
subprocess.run([FF, "-y", "-f", "lavfi", "-i", "sine=f=200:duration=2", music], check=True, capture_output=True)

w = Main(app)
w.show()


def wait(timeout=90000):
    loop = QEventLoop()
    w.worker.all_done.connect(loop.quit)
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    app.processEvents()


def tidy(s):
    return s.encode("ascii", "replace").decode()


w.add_paths([src])
assert w.listw.count() == 1
w.outdir.setText(os.path.join(d, "out"))

# 1) full processing with effects + music + watermark-less
w.o_orient.setCurrentText("Portrait 9:16"); w.o_quality.setCurrentText("480p")
w.o_look.setCurrentText("Cinematic (teal & orange)"); w.sl["contrast"].setValue(10)
w.music.edit.setText(music)
w.process(); wait()
out = os.path.join(d, "out", "clip_enhanced.mp4")
print(tidy(w.log.toPlainText()))
i = media_info(out); assert i["w"] == 480 and i["has_audio"] and w.bar.value() == 100, i
print("process ok")

# 2) tools
w.music.edit.clear()
w.x_fmt.setCurrentText("MP3"); w.run_tool("split"); wait()
assert os.path.exists(os.path.join(d, "out", "clip_audio.mp3")) and os.path.exists(os.path.join(d, "out", "clip_video_only.mp4"))
w.a_file.edit.setText(music); w.run_tool("add"); wait()
assert media_info(os.path.join(d, "out", "clip_newaudio.mp4"))["has_audio"]
print("tools ok")

# 3) preview renders and the player window opens
w.o_quality.setCurrentText("1080p (Full HD)")
w.preview(); wait()
import glob
pv = sorted(glob.glob(os.path.join(tempfile.gettempdir(), f"voxcut_preview_{os.getpid()}_*.mp4")))[-1]
assert os.path.exists(pv)
i = media_info(pv); assert i["h"] <= 860 and 1.5 < i["duration"] < 3.2, i
assert w.pv_dialog is not None; app.processEvents()
w.preview(); wait(); assert w.pv_n == 2   # second preview while first window was open
print("preview ok", i["h"], round(i["duration"], 1))

# 4) validation + theming + presets
w.t_start.setValue(5); w.t_end.setValue(2)
assert w.validate(w.settings())
w.t_start.setValue(0); w.t_end.setValue(0)
for t in ("Graphite", "Ocean", "Sunset", "Forest", "Light", "Midnight"):
    w.o_theme.setCurrentText(t); app.processEvents()
w.set_accent("#F25CA8"); w.reset_accent()
w.preset.setCurrentText("Podcast / voice clean-up (audio only)"); w.apply_preset()
assert w.c_audio_only.isChecked() and w.settings().target_lufs == -16
w.preset.setCurrentText("TikTok / Reels / Shorts - portrait"); w.apply_preset()
assert w.settings().orientation == "Portrait 9:16" and not w.c_audio_only.isChecked()
print("ui ok")
print("GUI OK")
