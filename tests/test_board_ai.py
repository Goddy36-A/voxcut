"""Board AI client, parsing, shape conversion and dialog - against a local mock of the board API (no network)."""
import base64, io, json, os, sys, tempfile, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer
from PySide6.QtWidgets import QApplication

from voxcut import board_ai as B
from voxcut.cloud import CloudError, NotSignedIn

SEEN = []
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
_img = QImage(8, 6, QImage.Format_ARGB32); _img.fill(QColor("#2f6bff")); _buf = QBuffer(); _buf.open(QIODevice.WriteOnly)
_img.save(_buf, "PNG"); PNG1 = base64.b64encode(bytes(_buf.data())).decode()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((self.path, self.headers.get("x-api-key"), self.headers.get("User-Agent"), body))
        if self.headers.get("x-api-key") != "board-key":
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"bad key"}'); return
        out = {"transcription": {"text": "f(x) = x^2", "latex": "f'(x) = 2x"},
               "explanation": {"title": "Derivatives", "summary": "The slope of a curve.",
                               "key_points": ["Power rule", "Slope = rate of change"],
                               "quiz": ["What is d/dx of x^2?", {"question": "Why does the power rule work?"}]}}
        if "finish" in body["tasks"]:
            out["finish"] = {"shapes": [{"type": "box", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.2, "text": "Input"},
                                        {"type": "arrow", "from": {"x": 0.4, "y": 0.2}, "to": {"x": 0.6, "y": 0.2}},
                                        {"type": "label", "x": 0.62, "y": 0.18, "text": "Output"},
                                        {"type": "mystery", "x": 1}],
                             "preview": "data:image/png;base64," + PNG1}
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(out).encode())


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
PNG = base64.b64decode(PNG1)

# 1) request shape matches the owner's example (x-api-key, data URL, tasks, lens, context)
c = B.BoardClient("board-key", base)
res = c.analyze(PNG, ["read", "explain"], "socratic", "Calculus: derivatives")
p, key, ua, body = SEEN[-1]
assert p == "/api/public/v1/board" and key == "board-key" and ua.startswith("IdeawoodStudio/")
assert body["image"].startswith("data:image/png;base64,") and base64.b64decode(body["image"].split(",")[1]) == PNG
assert body["tasks"] == ["read", "explain"] and body["lens"] == "socratic" and body["context"] == "Calculus: derivatives"
assert res["transcription"]["latex"] == "f'(x) = 2x"                              # as in the example script
assert res["explanation"]["quiz"][0] == "What is d/dx of x^2?"

# 2) errors: no key, wrong key
try: B.BoardClient("", base).analyze(PNG, ["read"]); raise SystemExit("expected")
except NotSignedIn: pass
try: B.BoardClient("nope", base).analyze(PNG, ["read"]); raise SystemExit("expected")
except NotSignedIn as e: assert "401" in str(e) and "bad key" in str(e)

# 3) lenient parsing
t, e = B.parse_transcription(res), B.parse_explanation(res)
assert t == {"text": "f(x) = x^2", "latex": "f'(x) = 2x"}
assert e["title"] == "Derivatives" and len(e["key_points"]) == 2 and e["quiz"][1] == "Why does the power rule work?"
assert B.parse_transcription({"transcription": "plain"}) == {"text": "plain", "latex": ""}
assert B.parse_explanation({}) == {"title": "", "summary": "", "key_points": [], "quiz": []}
fin = B.parse_finish(c.analyze(PNG, ["finish"]))
assert len(fin["shapes"]) == 4 and fin["preview"] == PNG

# 4) shapes -> whiteboard items (fractions scaled to the sent image; unknown shapes skipped)
items = B.shapes_to_items(fin["shapes"], 1000, 500)
kinds = [i.kind for i in items]
assert kinds == ["rect", "text", "arrow", "text"], kinds
rnd = lambda it: tuple((round(x, 3), round(y, 3)) for x, y in it.pts)
assert rnd(items[0]) == ((100.0, 50.0), (400.0, 150.0)) and items[1].text == "Input"
assert rnd(items[2]) == ((400.0, 100.0), (600.0, 100.0))
px = B.shapes_to_items([{"kind": "circle", "x1": 50, "y1": 60, "x2": 150, "y2": 160}], 800, 600)   # pixel coordinates
assert px[0].kind == "ellipse" and px[0].pts == ((50.0, 60.0), (150.0, 160.0))

# 5) notes export
md = B.notes_markdown("Calculus", t, e)
assert "# Derivatives" in md and "- Power rule" in md and "```latex" in md and "1. What is d/dx of x^2?" in md

# 6) dialog flow through the whiteboard (key, run, results, add shapes on a new page, notes)
app = QApplication.instance() or QApplication([])
from voxcut.whiteboard import WhiteboardWindow
import voxcut.board_ai as BA
BA.BASE = base
orig = BA.BoardClient.__init__
BA.BoardClient.__init__ = lambda self, k="", b=base: orig(self, k, b)
from voxcut.board_ai_ui import BoardAIDialog, png_for_upload
wb = WhiteboardWindow(); wb.resize(900, 600); wb.show()
from voxcut.drawing import Item
wb.canvas.model.add(Item("pen", ((10, 10), (200, 80), (300, 20))))
png, w, h, scale = png_for_upload(wb.canvas)
assert png[:8] == b"\x89PNG\r\n\x1a\n" and w >= 640 and scale == 1.0
d = BoardAIDialog(wb.canvas); d.qs.remove("board_key")
d.key.setText("board-key"); d.remember.setChecked(False); d.context.setText("Calculus")
def wait(cond, s=15):
    t0 = time.time()
    while not cond() and time.time() - t0 < s: app.processEvents(); time.sleep(0.02)
d.btns["both"].click(); wait(lambda: d.worker is None)
assert "f'(x) = 2x" in d.t_latex.toPlainText() and "Power rule" in d.e_view.toPlainText()
d.btns["finish"].click(); wait(lambda: d.worker is None)
assert d.s_add.isEnabled() and "4 editable" in d.s_info.text(), d.s_info.text()
n_pages = len(wb.canvas.pages)
d.s_add.click()
assert len(wb.canvas.pages) == n_pages + 1 and [i.kind for i in wb.canvas.model.items] == ["rect", "text", "arrow", "text"]
d.key.setText("wrong")
import voxcut.board_ai_ui as UI
UI.QMessageBox.warning = staticmethod(lambda *a, **k: None)
d.btns["read"].click(); wait(lambda: d.worker is None)
assert d.status.text().startswith("Failed") and "401" in d.status.text()
print("board ai tests OK")
