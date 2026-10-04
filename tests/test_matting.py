import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voxcut.engine import Settings, plan_enhance, media_info
from voxcut.paths import find_tool
from voxcut.matting import available

FF = find_tool("ffmpeg")
HERE = os.path.dirname(os.path.abspath(__file__))


def px(path, x, y, t=0.3):
    r = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", path, "-vf", f"format=rgb24,crop=1:1:{x}:{y}", "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return tuple(r.stdout[:3])


def dist(a, b):
    return sum(abs(i - j) for i, j in zip(a, b))


def run(src, out, s, preview=None):
    runner, _ = plan_enhance(src, out, s, preview)
    prog = []
    runner.run(prog.append)
    assert prog and prog[-1] == 100.0
    return media_info(out)


def test_all():
    ok, why = available()
    assert ok, why
    d = tempfile.mkdtemp()
    P = lambda n: os.path.join(d, n)
    # a 1.2 s "recording" of a real person photo, with a voice-like tone
    subprocess.run([FF, "-y", "-loop", "1", "-i", os.path.join(HERE, "data", "person.png"), "-f", "lavfi", "-i",
                    "sine=f=300:duration=1.2", "-t", "1.2", "-r", "25", "-pix_fmt", "yuv420p", "-shortest", P("me.mp4")],
                   check=True, capture_output=True)
    subprocess.run([FF, "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360", "-frames:v", "1", P("campus.png")], check=True, capture_output=True)
    subprocess.run([FF, "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=0.5:r=25", "-pix_fmt", "yuv420p", P("loop.mp4")], check=True, capture_output=True)
    base = dict(quality="480p", fps=25, mat_edge=20)
    green, red, blue = (0, 177, 64), (255, 0, 0), (0, 0, 255)

    # solid colour: corners become the colour, person (face / suit) is NOT replaced
    i = run(P("me.mp4"), P("o1.mp4"), Settings(mat_mode="Solid colour", mat_color="#00B140", **base))
    assert (i["w"], i["h"]) == (480, 480) and i["has_audio"] and 0.9 < i["duration"] < 1.5, i
    assert dist(px(P("o1.mp4"), 4, 4), green) < 70, px(P("o1.mp4"), 4, 4)
    assert dist(px(P("o1.mp4"), 210, 100), green) > 150, "face was removed"
    assert dist(px(P("o1.mp4"), 240, 330), green) > 150, "body was removed"
    print("solid colour ok")

    # image background (landscape photo cropped to fit)
    run(P("me.mp4"), P("o2.mp4"), Settings(mat_mode="Image", mat_path=P("campus.png"), **base))
    assert dist(px(P("o2.mp4"), 4, 4), red) < 90, px(P("o2.mp4"), 4, 4)
    assert dist(px(P("o2.mp4"), 240, 330), red) > 150
    print("image background ok")

    # looping video background shorter than the clip
    run(P("me.mp4"), P("o3.mp4"), Settings(mat_mode="Video", mat_path=P("loop.mp4"), **base))
    assert dist(px(P("o3.mp4"), 4, 4, 0.9), blue) < 90, px(P("o3.mp4"), 4, 4, 0.9)
    print("video background ok")

    # blur my room + lighting match off
    i = run(P("me.mp4"), P("o4.mp4"), Settings(mat_mode="Blur my room", mat_light=False, **base))
    assert i["has_audio"] and i["has_video"]
    print("blur ok")

    # with other features at the same time: portrait, effects, trim, speed, music-free, preview
    s = Settings(mat_mode="Solid colour", orientation="Portrait 9:16", color_preset="Warm", trim_start=0.2, trim_end=1.0, **base)
    i = run(P("me.mp4"), P("o5.mp4"), s)
    assert (i["w"], i["h"]) == (480, 854) or i["w"] == 480, i
    assert 0.6 < i["duration"] < 1.0, i
    i = run(P("me.mp4"), P("o6.mp4"), Settings(mat_mode="Solid colour", **base), preview=(0.0, 0.8))
    assert i["h"] <= 482 and 0.6 < i["duration"] < 1.0, i
    print("combined + preview ok")

    # leftovers are cleaned up
    assert not [f for f in os.listdir(tempfile.gettempdir()) if f.startswith("ideawood_bg_")]
    print("MATTING OK")


if __name__ == "__main__":
    test_all()
