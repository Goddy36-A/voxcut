import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voxcut.engine import Settings, build_command, Job, media_info
from voxcut.paths import find_tool

FF = find_tool("ffmpeg")


def make(path, w, h, secs, audio=True):
    cmd = [FF, "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=25:duration={secs}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:duration={secs}", "-f", "lavfi",
                "-i", f"anoisesrc=d={secs}:a=0.05", "-filter_complex", "[1][2]amix=inputs=2[a]", "-map", "0:v", "-map", "[a]"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", path]
    subprocess.run(cmd, check=True, capture_output=True)


def run(main, out, s):
    cmd, dur = build_command(main, out, s)
    Job(cmd, dur).run()
    return media_info(out)


def test_all():
    d = tempfile.mkdtemp()
    main, intro, outro = (os.path.join(d, n) for n in ("m.mp4", "i.mp4", "o.mp4"))
    make(main, 1280, 720, 3)
    make(intro, 640, 360, 1, audio=False)
    make(outro, 640, 360, 1)
    # landscape -> portrait, blurred bg, intro+outro, 720p
    s = Settings(orientation="Portrait 9:16", quality="720p (HD)", intro=intro, outro=outro,
                 compression="Small file", codec="H.265 / HEVC (smaller)")
    i = run(main, os.path.join(d, "a.mp4"), s)
    assert (i["w"], i["h"]) == (720, 1280) and i["has_audio"] and 4.5 < i["duration"] < 5.5, i
    for fit in ("Solid colour background", "Crop to fill"):
        i = run(main, os.path.join(d, "b.mp4"), Settings(orientation="Square 1:1", quality="480p", fit_mode=fit))
        assert (i["w"], i["h"]) == (480, 480)
    # image background
    img = os.path.join(d, "bg.png")
    subprocess.run([FF, "-y", "-f", "lavfi", "-i", "color=c=blue:s=400x400", "-frames:v", "1", img], check=True, capture_output=True)
    i = run(main, os.path.join(d, "c.mp4"), Settings(orientation="Portrait 9:16", quality="480p", fit_mode="Image background", bg_image=img))
    assert (i["w"], i["h"]) == (480, 854) or i["h"] in (852, 854)
    # audio only
    i = run(main, os.path.join(d, "d.mp4"), Settings(audio_only_enhance=True))
    assert (i["w"], i["h"]) == (1280, 720)
    # keep original + upscale to 1080p
    i = run(main, os.path.join(d, "e.mp4"), Settings(quality="1080p (Full HD)"))
    assert (i["w"], i["h"]) == (1920, 1080)
    print("ALL OK")


if __name__ == "__main__":
    test_all()
