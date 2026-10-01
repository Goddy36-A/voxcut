import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voxcut.engine import (Settings, build_command, Job, media_info, COLOR_PRESETS, plan_extract_audio,
                           plan_remove_audio, plan_add_audio)
from voxcut.paths import find_tool

FF = find_tool("ffmpeg")


def make(path, w, h, secs, audio=True):
    cmd = [FF, "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=25:duration={secs}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:duration={secs}", "-f", "lavfi",
                "-i", f"anoisesrc=d={secs}:a=0.05", "-filter_complex", "[1][2]amix=inputs=2[a]", "-map", "0:v", "-map", "[a]"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", path]
    subprocess.run(cmd, check=True, capture_output=True)


def run(cmd_dur):
    cmd, dur = cmd_dur
    Job(cmd, dur).run()


def test_all():
    d = tempfile.mkdtemp()
    P = lambda n: os.path.join(d, n)
    make(P("m.mp4"), 1280, 720, 4)
    make(P("i.mp4"), 640, 360, 1, audio=False)
    make(P("o.mp4"), 640, 360, 1)
    subprocess.run([FF, "-y", "-f", "lavfi", "-i", "sine=f=220:duration=2", P("music.mp3")], check=True, capture_output=True)
    subprocess.run([FF, "-y", "-f", "lavfi", "-i", "color=c=red:s=300x100", "-frames:v", "1", P("logo.png")], check=True, capture_output=True)
    subprocess.run([FF, "-y", "-f", "lavfi", "-i", "color=c=blue:s=400x400", "-frames:v", "1", P("bg.png")], check=True, capture_output=True)
    main = P("m.mp4")

    # 1) layout: landscape -> portrait, intro+outro, HEVC
    s = Settings(orientation="Portrait 9:16", quality="720p (HD)", intro=P("i.mp4"), outro=P("o.mp4"),
                 compression="Small file", codec="H.265 / HEVC (smaller)")
    run(build_command(main, P("a.mp4"), s)); i = media_info(P("a.mp4"))
    assert (i["w"], i["h"]) == (720, 1280) and i["has_audio"] and 5.5 < i["duration"] < 6.5, i
    print("layout/intro/outro ok")

    # 2) every colour preset
    for name in COLOR_PRESETS:
        run(build_command(main, P("c.mp4"), Settings(quality="480p", color_preset=name, trim_end=1)))
        assert media_info(P("c.mp4"))["h"] == 480, name
    print("colour presets ok:", len(COLOR_PRESETS))

    # 3) all manual effects at once
    s = Settings(quality="480p", brightness=10, contrast=15, saturation=20, gamma=10, warmth=20, sharpen=40,
                 vignette=True, video_denoise=True, mirror=True, fade_in=0.5, fade_out=0.5)
    run(build_command(main, P("e.mp4"), s)); assert media_info(P("e.mp4"))["has_video"]
    print("manual effects ok")

    # 4) speed + trim
    s = Settings(quality="480p", speed=2.0, trim_start=1, trim_end=3)
    run(build_command(main, P("sp.mp4"), s)); i = media_info(P("sp.mp4"))
    assert 0.8 < i["duration"] < 1.3 and i["has_audio"], i
    print("speed/trim ok", i["duration"])

    # 5) music (looped, ducked) + watermark
    s = Settings(quality="480p", music=P("music.mp3"), music_volume=30, watermark=P("logo.png"), wm_position="Top right")
    run(build_command(main, P("mu.mp4"), s)); i = media_info(P("mu.mp4"))
    assert 3.8 < i["duration"] < 4.3 and i["has_audio"], i
    s = Settings(quality="480p", music=P("music.mp3"), music_duck=False)
    run(build_command(main, P("mu2.mp4"), s)); assert 3.8 < media_info(P("mu2.mp4"))["duration"] < 4.3
    print("music/watermark ok")

    # 6) other fit modes + image bg
    for fit in ("Solid colour background", "Crop to fill", "Image background"):
        run(build_command(main, P("b.mp4"), Settings(orientation="Square 1:1", quality="480p", fit_mode=fit, bg_image=P("bg.png"), trim_end=1)))
        assert (media_info(P("b.mp4"))["w"], media_info(P("b.mp4"))["h"]) == (480, 480)
    print("fit modes ok")

    # 7) audio-only fast path (+ music)
    run(build_command(main, P("d.mp4"), Settings(audio_only_enhance=True, music=P("music.mp3"))))
    i = media_info(P("d.mp4")); assert (i["w"], i["h"]) == (1280, 720) and 3.8 < i["duration"] < 4.3, i
    print("audio-only ok")

    # 8) preview
    run(build_command(main, P("pv.mp4"), Settings(orientation="Portrait 9:16", intro=P("i.mp4"), speed=1.0), preview=(1, 2)))
    i = media_info(P("pv.mp4")); assert i["h"] == 854 or i["h"] == 852, i
    assert 1.8 < i["duration"] < 2.3, i
    print("preview ok", i)

    # 9) tools
    run(plan_extract_audio(main, P("x.mp3"), "MP3", True, Settings())); assert media_info(P("x.mp3"))["duration"] > 3.5
    run(plan_extract_audio(main, P("x.wav"), "WAV (lossless)", False, Settings())); assert os.path.getsize(P("x.wav")) > 1000
    run(plan_remove_audio(main, P("mute.mp4"))); i = media_info(P("mute.mp4")); assert i["has_video"] and not i["has_audio"]
    run(plan_add_audio(P("mute.mp4"), P("music.mp3"), P("rep.mp4"), "replace", 100, True))
    i = media_info(P("rep.mp4")); assert i["has_audio"] and 3.8 < i["duration"] < 4.3, i
    run(plan_add_audio(main, P("music.mp3"), P("mix.mp4"), "mix", 50, True))
    i = media_info(P("mix.mp4")); assert i["has_audio"] and 3.8 < i["duration"] < 4.3, i
    print("tools ok")
    print("ALL OK")


if __name__ == "__main__":
    test_all()
