"""Subtitles tab and workspaces, end to end through the UI (recognition itself is faked; ffmpeg is real)."""
import os, subprocess, sys, tempfile, threading, time, json
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = tempfile.mkdtemp()
os.environ["IDEAWOOD_MODELS"] = os.path.join(T, "models")
from http.server import BaseHTTPRequestHandler, HTTPServer
from PySide6.QtWidgets import QApplication
from voxcut import asr, subtitles_ui as SU
_orig_dirs = asr.bundled_models_dirs        # hermetic: only a bundled dir given through the env var counts (CI has the real one)
asr.bundled_models_dirs = lambda: [d for d in _orig_dirs() if d == os.environ.get("IDEAWOOD_BUNDLED_MODELS")]
from voxcut.engine import media_info
from voxcut.paths import find_tool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
src_t = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_asr.py")).read().split("# 1) parsing")[0]
exec(src_t)                                   # SAMPLE (whisper JSON layout)
TR = asr.parse_whisper_json(SAMPLE)

app = QApplication.instance() or QApplication([])
from PySide6.QtCore import QSettings
QSettings("Ideawood", "IdeawoodStudio").clear()          # hermetic: no state from earlier runs
from voxcut.gui import Main
w = Main(app); sp = w.subtitles
ff = find_tool("ffmpeg")
video = os.path.join(T, "talk.mp4")
subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=14", "-f", "lavfi",
                "-i", "sine=f=300:duration=14", "-shortest", "-pix_fmt", "yuv420p", video], check=True)
info_msgs, out_path = [], [""]
SU.QMessageBox.information = staticmethod(lambda *a, **k: info_msgs.append(a[2]))
SU.QMessageBox.warning = staticmethod(lambda *a, **k: info_msgs.append("WARN " + a[2]))
SU.QMessageBox.question = staticmethod(lambda *a, **k: SU.QMessageBox.Yes)
SU.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out_path[0], ""))


def wait(cond, s=60):
    t0 = time.time()
    while not cond() and time.time() - t0 < s:
        app.processEvents(); time.sleep(0.02)
    assert cond(), "timeout"


# 0) no model yet -> friendly message, nothing runs
sp.load_file(video)
sp.create(); assert sp.worker is None and any("Download the speech model" in m for m in info_msgs)

# 1) model download (resume-capable downloader against a local server) through the UI
payload = os.urandom(2_000_000)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
asr.MODEL_URL = f"http://127.0.0.1:{srv.server_port}/{{file}}"
asr.MODELS["base.en"] = ("Base, English only", "ggml-base.en.bin", 2, False)
sp._fill_models(); sp.model.setCurrentIndex(sp.model.findData("base.en")); sp._model_changed()
assert "Not downloaded" in sp.model_status.text() and not sp.dl_btn.isHidden()
sp.download(); wait(lambda: sp.worker is None)
assert "base.en" in asr.installed_models() or os.path.getsize(asr.model_file("base.en")) == len(payload)
open(asr.model_file("base.en"), "ab").write(b"\0" * 10_000_000)            # make it count as installed (>10 MB)
sp._fill_models(); assert "✓" in sp.model.currentText() and sp.dl_btn.isHidden()

# 2) language gate: English-only model + Swahili -> asks for a multilingual model
sp.lang.setCurrentText("Swahili"); sp.create()
assert sp.worker is None and any("English only" in m for m in info_msgs)
sp.lang.setCurrentText("English")

# 3) create subtitles (recognition faked, everything else real)
asr.WhisperEngine.transcribe = lambda self, wav, language="auto", prompt="", on_progress=lambda p: None, should_cancel=lambda: False, hide_nonspeech=True: (on_progress(100.0), TR)[1]
sp.create(); wait(lambda: sp.worker is None)
assert sp.table.rowCount() == len(sp.cues) >= 3 and "Done:" in sp.status.text()
assert sp.maxchars.value() == asr.auto_max_chars(640, 360, "Medium")
first = sp.table.item(0, 3).text(); assert first.startswith("And so my fellow Americans,")

# 4) editing tools
n0 = sp.table.rowCount()
sp.table.item(0, 3).setText("Hello | world")                       # manual edit with a line break
assert sp.read_cues()[0].text == "Hello\nworld" and sp.read_cues()[0].words == []
sp.table.setCurrentCell(1, 0); sp.split(); assert sp.table.rowCount() == n0 + 1
sp.table.setCurrentCell(1, 0); sp.merge(); assert sp.table.rowCount() == n0
sp.find.setText("Thank you"); sp.repl.setText("Thanks"); sp.replace_all()
assert any(c.text.startswith("Thanks all") for c in sp.read_cues())
t_before = sp.read_cues()[1].start; sp.shift.setValue(0.5); sp.shift_all()
assert abs(sp.read_cues()[1].start - (t_before + 0.5)) < 0.01
sp.table.item(1, 1).setText("not a time"); assert asr.parse_srt and sp.table.item(1, 1).text() != "not a time"   # invalid -> restored
sp.table.setCurrentCell(0, 0); sp.delete_row(); assert sp.table.rowCount() == n0 - 1
assert SU.parse_time("1:02:03,5") == 3723.5 and SU.parse_time("02:03.250") == 123.25 and SU.parse_time("x") is None
sp.resplit(); assert sp.table.item(0, 3).text().startswith("And so my fellow")     # rebuilt from the transcript

# 5) saving
for kind, marker in (("srt", "-->"), ("vtt", "WEBVTT"), ("txt", "Thank you")):
    out_path[0] = os.path.join(T, f"out.{kind}"); sp.save(kind)
    assert marker in open(out_path[0], encoding="utf-8-sig").read(), kind
assert len(asr.parse_srt(open(os.path.join(T, "out.srt"), encoding="utf-8-sig").read())) == sp.table.rowCount()

# 6) burn into video (karaoke + box) and soft track
sp.hl.setChecked(True); sp.pos.setCurrentText("Bottom"); sp.size.setCurrentText("Large"); sp.color.setCurrentText("Yellow")
out_path[0] = os.path.join(T, "burned.mp4"); sp.burn(); wait(lambda: sp.worker is None)
bi = media_info(out_path[0]); assert bi["has_video"] and bi["has_audio"] and (bi["w"], bi["h"]) == (640, 360)
def frame(p, t):
    return subprocess.run([ff, "-v", "error", "-ss", str(t), "-i", p, "-frames:v", "1", "-vf", "crop=640:140:0:220,format=gray",
                           "-f", "rawvideo", "-"], capture_output=True).stdout
a, b = frame(video, 1.5), frame(out_path[0], 1.5)
assert sum(1 for x, y in zip(a, b) if abs(x - y) > 40) > 400, "captions not visible in the burned video"
out_path[0] = os.path.join(T, "soft.mp4"); sp.embed(); wait(lambda: sp.worker is None)
pr = subprocess.run([find_tool("ffprobe"), "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", out_path[0]],
                    capture_output=True, text=True).stdout
assert "mov_text" in pr, pr
sp.load_file(os.path.join(T, "out.srt")); assert sp.src.endswith("out.srt")
out_path[0] = os.path.join(T, "nope.mp4"); n_msgs = len(info_msgs); sp.burn()
assert len(info_msgs) > n_msgs                                   # an .srt is not a video -> friendly message

# 6b) a model shipped inside the app: listed as built in, no download button, usable at once
bund = os.path.join(T, "bundled"); os.makedirs(bund, exist_ok=True)
open(os.path.join(bund, "ggml-small.en.bin"), "wb").write(b"\0" * 11_000_000)
os.environ["IDEAWOOD_BUNDLED_MODELS"] = bund
sp.qs.setValue("asr_model", "small.en"); sp._fill_models()
assert "built in" in sp.model.currentText() and sp.dl_btn.isHidden() and "Built into" in sp.model_status.text()
assert sp._model_path() == os.path.join(bund, "ggml-small.en.bin")

# 7) recorder -> subtitles hand-off, and workspaces
w.lecture.result_path = video; w.lecture.to_subtitles()
assert w.nav.currentItem().text() == "Subtitles" and sp.src == video
w.workspace.setCurrentText("Radio / Podcast"); w.apply_workspace(True)
assert w.nav.currentItem().text() == "Subtitles" and sp.pos.currentText() == "Middle" and sp.hl.isChecked()
assert w.lecture.name.text() == "Episode" and w.mk_tpl.count() >= 2
w.workspace.setCurrentText("Business / Marketing"); w.apply_workspace(True)
assert w.nav.currentItem().text() == "Create video (AI)"
w.mk_tpl.setCurrentIndex(1); w.mk_tpl.activated.emit(1)
assert "[product]" in w.mk_prompt.toPlainText()
w.qs.clear(); w.close()
print("subtitles ui tests OK")
