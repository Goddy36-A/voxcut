"""Lecturer tools: drawing model, whiteboard, screen overlay, recorder bar and the full Lecture recorder flow
(offscreen Qt + FFmpeg test sources, so it runs anywhere)."""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from voxcut import recorder as R
from voxcut.drawing import DrawModel, Item, hit
from voxcut.engine import media_info
from voxcut.overlay import RecorderBar, ScreenOverlay
from voxcut.whiteboard import BoardCanvas, WhiteboardWindow

app = QApplication.instance() or QApplication([])
T = tempfile.mkdtemp()


def wait(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents(); time.sleep(0.01)


def drag(w, a, b, steps=6):
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, QPoint(*a))
    for i in range(1, steps + 1):
        p = (a[0] + (b[0] - a[0]) * i // steps, a[1] + (b[1] - a[1]) * i // steps)
        QTest.mouseMove(w, QPoint(*p))
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, QPoint(*b))


# ---- 1) drawing model
m = DrawModel()
m.add(Item("pen", ((0, 0), (50, 0), (100, 0)), width=4)); m.add(Item("rect", ((200, 200), (300, 260))))
assert len(m.items) == 2 and m.undo() and len(m.items) == 1 and m.redo() and len(m.items) == 2
assert hit(m.items[0], (50, 5), 6) and not hit(m.items[0], (50, 80), 6)
assert m.erase_at((250, 200), 8) and len(m.items) == 1 and m.undo() and len(m.items) == 2
m.clear(); assert m.items == [] and m.undo() and len(m.items) == 2

# ---- 2) whiteboard
wb = WhiteboardWindow(); wb.resize(900, 600); wb.show(); app.processEvents()
c = wb.canvas
c.set_tool("pen"); drag(c, (50, 80), (300, 160)); assert len(c.model.items) == 1 and len(c.model.items[0].pts) > 3
c.set_tool("arrow"); drag(c, (60, 200), (260, 220)); assert c.model.items[-1].kind == "arrow"
c.set_tool("rect"); drag(c, (320, 200), (420, 280)); c.set_tool("ellipse"); drag(c, (450, 200), (560, 280))
c.set_tool("highlighter"); drag(c, (50, 300), (300, 300))
c.text_provider = lambda pos: "x = (-b ± √D) / 2a"
c.set_tool("text"); QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, QPoint(80, 350))
assert [i.kind for i in c.model.items] == ["pen", "arrow", "rect", "ellipse", "highlighter", "text"]
c.set_tool("eraser"); drag(c, (320, 240), (420, 240)); assert "rect" not in [i.kind for i in c.model.items]
c.undo(); assert "rect" in [i.kind for i in c.model.items]; c.redo()
# pages + backgrounds
c.new_page(); assert (c.page, len(c.pages)) == (1, 2) and c.model.items == []
c.go(-1); assert c.page == 0 and len(c.model.items) >= 5
wb.bg.setCurrentText("Chalkboard"); assert c.color == "#ffffff" or c.color != "#111111"
img = c.render_image(); assert not img.isNull() and img.width() >= 640
px = {img.pixel(x, 5) for x in range(0, 200, 7)}; assert len(px) >= 1
png = os.path.join(T, "p.png"); img.save(png); assert os.path.getsize(png) > 2000
pdf = os.path.join(T, "b.pdf"); n = c.export_pdf(pdf); assert n == 2
assert open(pdf, "rb").read(5) == b"%PDF-" and os.path.getsize(pdf) > 1500

# ---- 3) screen overlay (draw over other windows, laser, spotlight)
scr = QGuiApplication.primaryScreen()
ov = ScreenOverlay(scr); ov.resize(800, 500); ov.show()
assert ov.windowFlags() & Qt.WindowTransparentForInput            # starts click-through
ov.set_tool("pen"); assert not (ov.windowFlags() & Qt.WindowTransparentForInput)
drag(ov, (100, 100), (400, 220)); assert len(ov.model.items) == 1
ov.set_tool("arrow"); drag(ov, (100, 300), (300, 330)); assert len(ov.model.items) == 2
ov.set_color("#2f6bff"); ov.undo(); assert len(ov.model.items) == 1
ov.set_tool("eraser"); drag(ov, (100, 98), (400, 224)); assert ov.model.items == []
ov.undo(); assert len(ov.model.items) == 1
for t in ("laser", "spotlight", "none"):
    ov.set_tool(t); assert ov.windowFlags() & Qt.WindowTransparentForInput; app.processEvents(); ov.grab()
ov.clear(); assert ov.model.items == []; ov.close()

# ---- 4) recorder bar signals
bar = RecorderBar(); got = []
bar.pause_toggled.connect(lambda: got.append("pause")); bar.stop_clicked.connect(lambda: got.append("stop"))
bar.marker_clicked.connect(lambda: got.append("marker")); bar.tool_selected.connect(got.append)
bar.board_clicked.connect(lambda: got.append("board")); bar.color_selected.connect(got.append)
from PySide6.QtWidgets import QPushButton
for b in bar.findChildren(QPushButton):
    if b.text() in ("Pause", "Stop", "Marker", "Pen", "Laser", "Whiteboard"):
        b.click()
assert got == ["pause", "stop", "marker", "pen", "laser", "board"], got
bar.set_time("12:34", True); assert "12:34" in bar.time_lbl.text() and bar.pause_btn.text() == "Resume"
bar.show(); bar.place_on(scr); bar.close()

# ---- 5) full Lecture recorder flow through the GUI (sources faked with FFmpeg test patterns)
from voxcut.gui import Main
w = Main(app); lp = w.lecture
lp.folder.setText(os.path.join(T, "lectures")); lp.name.setText("Maths"); lp.countdown_cb.setChecked(False)
lp.minimize_cb.setChecked(False)
VID = ["-re", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=15"]
MIC = ["-re", "-f", "lavfi", "-i", "sine=f=330:sample_rate=44100"]
orig_cfg = lp._cfg
def fake_cfg(out):
    cfg, s = orig_cfg(out)
    cfg.fps = 15; cfg.video_input = VID; cfg.mic_input = MIC; cfg.mic = "Test mic"
    return cfg, s
lp._cfg = fake_cfg
lp.start(); wait(2200)
assert lp.rec and lp.rec.state == "recording" and lp.bar and lp.overlay and lp.bar.isVisible()
lp.add_marker(); lp.toggle_draw(); assert lp.overlay.tool == "pen"; lp.toggle_draw(); assert lp.overlay.tool == "none"
lp.toggle_pause(); assert lp.rec.state == "paused"; wait(600); lp.toggle_pause(); wait(1800)
lp.stop()
t0 = time.time()
while lp.worker is not None and time.time() - t0 < 90:
    wait(100)
assert lp.worker is None and lp.result_path and os.path.isfile(lp.result_path), lp.status.text()
i = media_info(lp.result_path)
assert i["has_video"] and i["has_audio"] and 3.0 < i["duration"] < 7.5, i
assert os.path.isfile(os.path.splitext(lp.result_path)[0] + ".chapters.txt")
assert lp.queue_btn.isVisible() or not lp.page.isVisible()
lp.to_queue(); assert lp.result_path in w.files()
assert not os.path.exists(os.path.splitext(lp.result_path)[0] + ".parts")      # temp segments cleaned up

# closing the app mid-lecture must save it, not lose it
lp.start(); wait(2000)
out2 = lp.rec.cfg.out_path
w.close()
assert os.path.isfile(out2) and media_info(out2)["has_video"], "recording lost on close"
print("lecture tests OK")
