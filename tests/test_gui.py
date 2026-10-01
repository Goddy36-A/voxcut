import os, subprocess, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer
from voxcut.gui import Main
from voxcut.engine import media_info
from voxcut.paths import find_tool

app = QApplication([])
d = tempfile.mkdtemp()
src = os.path.join(d, "clip.mp4")
subprocess.run([find_tool("ffmpeg"), "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=2",
                "-f", "lavfi", "-i", "sine=f=300:duration=2", "-shortest", "-pix_fmt", "yuv420p", src],
               check=True, capture_output=True)
w = Main()
w.show()
w.listw.addItem(src)
w.o_orient.setCurrentText("Portrait 9:16")
w.o_quality.setCurrentText("480p")
w.start()
loop = QEventLoop()
w.worker.all_done.connect(loop.quit)
QTimer.singleShot(60000, loop.quit)
loop.exec()
out = os.path.join(d, "clip_enhanced.mp4")
print(w.log.toPlainText().encode('ascii', 'replace').decode())
assert os.path.exists(out), "no output"
i = media_info(out)
print(i)
assert (i["w"], i["h"]) == (480, 854) or i["w"] == 480
assert w.bar.value() == 100
print("GUI OK")
