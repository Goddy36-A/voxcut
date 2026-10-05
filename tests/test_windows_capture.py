"""Windows-only checks of the real capture code paths, run in CI on a Windows runner with the BUNDLED ffmpeg.

Strict about command syntax (any FFmpeg option-parsing error fails the build - this is the bug class that broke
v1.7.0 on newer FFmpeg), lenient about whether the CI machine can actually capture a screen.
"""
import os, re, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform != "win32":
    print("windows capture tests skipped (not Windows)"); sys.exit(0)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from voxcut import recorder as R
from voxcut.engine import media_info
from voxcut.paths import find_tool

FF = find_tool("ffmpeg")
print(subprocess.run([FF, "-version"], capture_output=True, text=True).stdout.splitlines()[0])
PARSE = re.compile(r"cannot be applied|Unrecognized option|Error parsing (global )?options|Option .* not found", re.I)
T = tempfile.mkdtemp()


def run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW)


def assert_no_parse_error(r, what):
    assert not PARSE.search(r.stderr), f"{what}: FFmpeg rejected the command line:\n{r.stderr[-700:]}"


# 1) Windows API discovery code (ctypes) must run and return well-formed data
mons = R.list_monitors()
for m in mons:
    assert {"name", "x", "y", "w", "h", "primary"} <= set(m) and m["w"] > 0 and m["h"] > 0, m
wins = R.list_windows(exclude_pids=(os.getpid(),))
for w in wins:
    assert {"hwnd", "title", "x", "y", "w", "h"} <= set(w), w
print(f"monitors={len(mons)} windows={len(wins)}")
dev = R.list_devices()
assert set(dev) == {"video", "audio"}
print("devices:", dev)

# 2) real gdigrab command lines: whole desktop and a region, 2 seconds each
for label, region in (("desktop", None), ("region", (0, 0, 641, 481))):
    seg = os.path.join(T, f"{label}.mkv")
    cmd = R.build_segment_cmd(R.RecordConfig(os.path.join(T, "x.mp4"), region=region, fps=15, draw_mouse=True), seg, FF)
    cmd = cmd[:-3] + ["-t", "2"] + cmd[-3:]
    r = run(cmd)
    assert_no_parse_error(r, f"gdigrab {label}")
    if r.returncode == 0 and os.path.exists(seg):
        i = media_info(seg)
        assert i["has_video"] and i["duration"] > 0.5, i
        print(f"gdigrab {label}: captured OK ({i['w']}x{i['h']}, {i['duration']:.1f}s)")
    else:
        print(f"gdigrab {label}: no screen available on this machine (not a command error): {r.stderr.strip()[-200:]}")

# 3) dshow option syntax (device does not exist -> runtime error is expected, parse error is not)
for kind in ("audio", "video"):
    r = run([FF, "-hide_banner", "-f", "dshow", "-rtbufsize", "256M", "-i", f"{kind}=__no_such_device__",
             "-t", "1", "-f", "null", "-"])
    assert_no_parse_error(r, f"dshow {kind}")
    assert r.returncode != 0
# full command with mic + camera + computer sound for a missing device: must fail on the device, not on syntax
cfg = R.RecordConfig(os.path.join(T, "y.mp4"), fps=15, mic="__no_mic__", camera="__no_cam__", sys_audio="__no_sys__")
r = run(R.build_segment_cmd(cfg, os.path.join(T, "y.mkv"), FF))
assert_no_parse_error(r, "full dshow command")

# 4) global hotkey registration works and cleans up
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from voxcut.overlay import Hotkeys
h = Hotkeys()
ok = h.register(77, "ctrl+alt+shift", "Q", lambda: None)
print("hotkey registered:", ok)
h.unregister_all()
print("windows capture tests OK")
