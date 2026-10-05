"""Recorder pipeline test with FFmpeg test sources instead of the real screen/mic/camera (works on any OS)."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voxcut.engine import media_info
from voxcut.recorder import (Recorder, RecorderError, RecordConfig, build_segment_cmd, parse_dshow_list, recover_parts,
                             write_chapters, clean_voice_cmd, fmt_time)
from voxcut.engine import Job

T = tempfile.mkdtemp()
VID = ["-re", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=15"]
MIC = ["-re", "-f", "lavfi", "-i", "sine=f=440:sample_rate=44100"]
CAM = ["-re", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=15"]

# 1) dshow device parsing - new ffmpeg format and old format
new = '''[dshow @ 0000] "Integrated Camera" (video)
[dshow @ 0000]   Alternative name "@device_pnp_\\\\?\\usb#vid"
[dshow @ 0000] "Microphone Array (Realtek)" (audio)
[dshow @ 0000]   Alternative name "@device_cm_{33D9}"
[dshow @ 0000] "Stereo Mix (Realtek)" (audio)
[dshow @ 0000] "Webcam Virtual" (none)'''
d = parse_dshow_list(new)
assert d == {"video": ["Integrated Camera"], "audio": ["Microphone Array (Realtek)", "Stereo Mix (Realtek)"]}, d
old = '''[dshow @ 0] DirectShow video devices
[dshow @ 0]  "USB2.0 Camera"
[dshow @ 0]     Alternative name "@device_pnp"
[dshow @ 0] DirectShow audio devices
[dshow @ 0]  "Mic (USB)"'''
assert parse_dshow_list(old) == {"video": ["USB2.0 Camera"], "audio": ["Mic (USB)"]}

# 2) Windows command shape (never executed here)
c = build_segment_cmd(RecordConfig("x.mp4", region=(100, 50, 1281, 721), mic="Mic (USB)", camera="USB2.0 Camera",
                                   sys_audio="Stereo Mix (Realtek)"), "seg.mkv")
j = " ".join(c)
assert "thread_queue_size" not in j          # removed in newer FFmpeg (input option rejected)
assert "gdigrab" in j and "-offset_x 100" in j and "-video_size 1280x720" in j      # even-sized region
assert "audio=Mic (USB)" in j and "video=USB2.0 Camera" in j and "amix=inputs=2" in j and " -t " not in j + " "

# 3) record -> mark -> pause -> resume -> stop  (+ webcam PiP, mic) -> one MP4, chapters, no quality loss
out = os.path.join(T, "lecture.mp4")
cfg = RecordConfig(out, fps=15, video_input=VID, mic_input=MIC, cam_input=CAM, camera="x", mic="x", cam_pos="Top left")
r = Recorder(cfg)
r.start()
time.sleep(3.0)
r.mark("Intro done")
r.pause(); assert r.state == "paused"
t_paused = r.elapsed(); time.sleep(1.5); assert abs(r.elapsed() - t_paused) < 0.05   # clock stops while paused
r.resume()
time.sleep(2.5)
r.mark()
res = r.stop()
i = media_info(out)
assert i["has_video"] and i["has_audio"] and (i["w"], i["h"]) == (1280, 720), i
assert abs(i["duration"] - r._done) < 1.6, (i, r._done)     # file length ~= recorded wall time (pause excluded)
assert r._done < 8.5                                        # the 1.5 s pause was NOT recorded
assert res["segments"] == 2 and not os.path.exists(r.parts_dir)
ch = open(res["chapters"]).read().splitlines()
assert ch[0] == "00:00 Start" and ch[1].startswith("00:03 Intro done") and ch[2].startswith("00:0") and "Chapter 2" in ch[2], ch

# 4) a bad input fails immediately with a readable message
try:
    Recorder(RecordConfig(os.path.join(T, "bad.mp4"), video_input=["-f", "lavfi", "-i", "nosuchsource=1"])).start()
    raise SystemExit("expected RecorderError")
except RecorderError as e:
    assert str(e)

# 5) crash recovery: kill ffmpeg without a clean stop, then rebuild from the .parts folder
out2 = os.path.join(T, "crash.mp4")
r2 = Recorder(RecordConfig(out2, fps=15, video_input=VID, mic_input=MIC, mic="x"))
r2.start(); time.sleep(2.5)
r2._proc.kill(); r2._proc.wait()
rec = recover_parts(r2.parts_dir)
i2 = media_info(rec["path"])
assert i2["has_video"] and i2["duration"] > 1.0, i2

# 6) voice-clean pass keeps video untouched and produces valid output
clean = os.path.join(T, "clean.mp4")
Job(clean_voice_cmd(out, clean), i["duration"]).run()
ic = media_info(clean)
assert ic["has_audio"] and (ic["w"], ic["h"]) == (1280, 720) and abs(ic["duration"] - i["duration"]) < 0.6, ic
assert fmt_time(3725) == "1:02:05" and fmt_time(65) == "01:05"
print("recorder tests OK")
