"""End-to-end test of the AI video maker with a fake cloud (no network). Uses a real compose sample."""
import json, os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from voxcut.cloud import CloudClient, CloudError
from voxcut.engine import media_info
from voxcut.maker import make_video, Cancelled
from voxcut.paths import find_tool

FF = find_tool("ffmpeg")
HERE = os.path.dirname(os.path.abspath(__file__))
PLAN = json.load(open(os.path.join(HERE, "sample_compose.json"), encoding="utf-8"))
app = QApplication.instance() or QApplication([])
T = tempfile.mkdtemp()


def ff(*args):
    subprocess.run([FF, "-y", "-loglevel", "error", *args], check=True)


class Fake(CloudClient):
    def __init__(self, video_bg=False, no_music=False):
        super().__init__(api_key="k")
        self.video_bg, self.no_music, self.calls = video_bg, no_music, []

    def compose(self, prompt):
        self.calls.append("compose"); return PLAN

    def tts(self, text, voice="alloy"):
        self.calls.append(f"tts:{voice}")
        if voice == "ballad":
            raise CloudError("voice not accepted")  # exercise the fallback to alloy
        p = os.path.join(T, "t.mp3"); ff("-f", "lavfi", "-i", "sine=f=300:duration=2.4", p)
        return open(p, "rb").read()

    def media_items(self, kind, query, orientation=None, page=1):
        self.calls.append(f"media:{kind}:{orientation}")
        if kind == "music" and self.no_music:
            return []
        if orientation == "vertical" and kind == "image":
            raise CloudError("bad orientation")  # exercise retry without orientation
        return [{"url": f"/x/{kind}", "thumb": "https://t/x", "credit": f"Artist {kind} · Pixabay"}]

    def download(self, url, dest, max_bytes=0):
        kind = url.rsplit("/", 1)[1]
        if kind == "image":
            p = dest + ".jpg"; ff("-f", "lavfi", "-i", "testsrc=size=1600x900", "-frames:v", "1", p)
        elif kind == "video":
            p = dest + ".mp4"; ff("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=2", "-pix_fmt", "yuv420p", p)
        else:
            p = dest + ".mp3"; ff("-f", "lavfi", "-i", "sine=f=220:duration=3", p)
        return p


two = dict(PLAN, items=PLAN["items"][:2])

# 1) portrait, image backgrounds forced, narration + music
plan_img = dict(two, backgroundKind="image")
out = os.path.join(T, "a.mp4"); status = []
c = Fake()
r = make_video(c, "p", out, plan=plan_img, on_status=status.append)
i = media_info(out)
assert i["has_video"] and i["has_audio"], i
assert (i["w"], i["h"]) == (1080, 1920), i
assert abs(i["duration"] - r["duration"]) < 0.6, (i, r)
assert r["duration"] >= 7.0 - 0.01, r            # 2 scenes, each at least 3.5s
assert "tts:ballad" in c.calls and "tts:alloy" in c.calls
assert any(x.startswith("media:music") for x in c.calls) and r["credits"]
assert os.path.exists(os.path.splitext(out)[0] + ".credits.txt")
assert status[0].startswith("Planning") and status[-1] == "Done"

# 2) landscape, video backgrounds, no narration, no music result -> note, still a valid video
out2 = os.path.join(T, "b.mp4")
r2 = make_video(Fake(video_bg=True, no_music=True), "p", out2, fmt="Landscape 16:9 (YouTube)", narrate=False,
                plan=dict(PLAN, items=PLAN["items"][:1], backgroundKind="video"))
i2 = media_info(out2)
assert (i2["w"], i2["h"]) == (1920, 1080) and i2["has_audio"], i2
assert abs(i2["duration"] - 7) < 0.7, i2            # perSlideSec = 7 when not narrating
assert any("Music skipped" in n for n in r2["notes"]), r2

# 3) cancel
try:
    make_video(Fake(), "p", os.path.join(T, "c.mp4"), plan=two, should_cancel=lambda: True)
    raise SystemExit("expected Cancelled")
except Cancelled:
    pass
print("maker tests OK")
