"""Lecture recording engine (FFmpeg).

* Windows capture: ``gdigrab`` (screen, any window area - other apps are recorded) + ``dshow`` (webcam, microphone,
  optional computer-sound device such as "Stereo Mix").
* No time limit: nothing here ever passes ``-t``. Audio/video are written in short-lived MKV *segments* inside a
  ``<name>.parts`` folder; Pause simply closes the segment, Resume starts a new one, Stop joins them into the final MP4
  without re-encoding. If the PC crashes mid-lecture, ``recover_parts`` rebuilds the video from the segments.
* Chapter markers become a YouTube-ready ``<name>.chapters.txt``.

The input arguments can be overridden through ``RecordConfig.*_input`` so the whole pipeline is testable with FFmpeg
test sources on any OS.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .paths import find_tool, no_window_flags

IS_WIN = sys.platform == "win32"
CAM_POSITIONS = ["Bottom right", "Bottom left", "Top right", "Top left"]
CAM_SIZES = {"Small": 0.16, "Medium": 0.22, "Large": 0.30}


# --------------------------------------------------------------------------- device discovery
def parse_dshow_list(text: str) -> dict:
    """Parse ``ffmpeg -list_devices true -f dshow -i dummy`` output (old and new ffmpeg formats)."""
    out = {"video": [], "audio": []}
    section = None
    for line in text.splitlines():
        low = line.lower()
        if "alternative name" in low:
            continue
        if "directshow video devices" in low:
            section = "video"; continue
        if "directshow audio devices" in low:
            section = "audio"; continue
        m = re.search(r'"([^"]+)"\s*\((video|audio|none)\)', line)
        if m and m.group(2) != "none":
            kind, name = m.group(2), m.group(1)
        else:
            m = re.search(r'\]\s+"([^"]+)"\s*$', line)
            if not m or not section:
                continue
            kind, name = section, m.group(1)
        if name not in out[kind]:
            out[kind].append(name)
    return out


def list_devices() -> dict:
    if not IS_WIN:
        return {"video": [], "audio": []}
    try:
        r = subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                           capture_output=True, text=True, errors="replace", timeout=20,
                           creationflags=no_window_flags())
        return parse_dshow_list(r.stderr + r.stdout)
    except Exception:  # noqa: BLE001
        return {"video": [], "audio": []}


def list_monitors() -> List[dict]:
    """Physical-pixel rectangles of every monitor (Windows). [{'name','x','y','w','h','primary'}]"""
    if not IS_WIN:
        return []
    import ctypes
    from ctypes import wintypes

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    res: List[dict] = []
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _lp):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            res.append({"name": mi.szDevice, "x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top,
                        "primary": bool(mi.dwFlags & 1)})
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    res.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    return res


def window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """Visible rectangle (x, y, w, h) in physical pixels of a window handle (Windows)."""
    if not IS_WIN:
        return None
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    # DWMWA_EXTENDED_FRAME_BOUNDS = 9 gives the real visible frame (no invisible resize border)
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
            return None
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def list_windows(exclude_pids=()) -> List[dict]:
    """Visible, titled, non-minimised top-level windows (Windows). [{'hwnd','title','x','y','w','h'}]"""
    if not IS_WIN:
        return []
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    res: List[dict] = []
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return True
        if u.GetWindowLongW(hwnd, -20) & 0x80:          # WS_EX_TOOLWINDOW
            return True
        cloaked = wintypes.DWORD()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in exclude_pids:
            return True
        rc = window_rect(hwnd)
        if rc and rc[2] >= 160 and rc[3] >= 120:
            res.append({"hwnd": int(hwnd), "title": buf.value, "x": rc[0], "y": rc[1], "w": rc[2], "h": rc[3]})
        return True

    u.EnumWindows(proc_t(cb), 0)
    return res


def exclude_from_capture(widget) -> bool:
    """Hide a Qt window from screen recordings (Windows 10 2004+). The toolbar must not appear in lectures."""
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11))  # WDA_EXCLUDEFROMCAPTURE
    except Exception:  # noqa: BLE001
        return False


def even(n: int) -> int:
    return max(2, int(n) // 2 * 2)


# --------------------------------------------------------------------------- configuration
@dataclass
class RecordConfig:
    out_path: str
    region: Optional[Tuple[int, int, int, int]] = None   # (x, y, w, h) physical px; None = whole desktop
    fps: int = 30
    quality: str = "light"          # light (low CPU, long lectures) | balanced
    max_width: int = 1920           # larger screens are scaled down to keep CPU and file size sane
    draw_mouse: bool = True
    mic: Optional[str] = None
    sys_audio: Optional[str] = None
    camera: Optional[str] = None
    cam_pos: str = "Bottom right"
    cam_size: str = "Medium"
    cam_res: str = ""               # e.g. "1280x720"; empty = device default
    # test/override hooks: full ffmpeg input argument lists
    video_input: Optional[List[str]] = None
    mic_input: Optional[List[str]] = None
    sys_input: Optional[List[str]] = None
    cam_input: Optional[List[str]] = None


def _screen_input(cfg: RecordConfig) -> List[str]:
    a = ["-thread_queue_size", "1024", "-f", "gdigrab", "-framerate", str(cfg.fps),
         "-draw_mouse", "1" if cfg.draw_mouse else "0"]
    if cfg.region:
        x, y, w, h = cfg.region
        a += ["-offset_x", str(int(x)), "-offset_y", str(int(y)), "-video_size", f"{even(w)}x{even(h)}"]
    return a + ["-i", "desktop"]


def _dshow(kind: str, name: str, extra: Optional[List[str]] = None) -> List[str]:
    return ["-thread_queue_size", "1024", "-f", "dshow", "-rtbufsize", "256M", *(extra or []), "-i", f"{kind}={name}"]


def build_segment_cmd(cfg: RecordConfig, seg_path: str, ff: str = "ffmpeg") -> List[str]:
    """One recording segment: screen (+ webcam picture-in-picture) + mic (+ computer sound) -> MKV."""
    inputs: List[str] = []
    n = 0
    inputs += cfg.video_input or _screen_input(cfg)
    vi = n; n += 1
    ci = None
    if cfg.camera or cfg.cam_input:
        extra = ["-video_size", cfg.cam_res] if cfg.cam_res else []
        inputs += cfg.cam_input or _dshow("video", cfg.camera, extra)
        ci = n; n += 1
    ai: List[int] = []
    if cfg.mic or cfg.mic_input:
        inputs += cfg.mic_input or _dshow("audio", cfg.mic)
        ai.append(n); n += 1
    if cfg.sys_audio or cfg.sys_input:
        inputs += cfg.sys_input or _dshow("audio", cfg.sys_audio)
        ai.append(n); n += 1

    maxw = even(cfg.max_width)
    chain = [f"[{vi}:v]scale='min({maxw},iw)':-2:flags=bicubic,setsar=1[base]"]
    last = "base"
    if ci is not None:
        frac = CAM_SIZES.get(cfg.cam_size, 0.22)
        chain.append(f"[{ci}:v]scale={even(int(maxw * frac))}:-2,setsar=1[cam]")
        pos = {"Bottom right": "W-w-24:H-h-24", "Bottom left": "24:H-h-24",
               "Top right": "W-w-24:24", "Top left": "24:24"}.get(cfg.cam_pos, "W-w-24:H-h-24")
        chain.append(f"[base][cam]overlay={pos}:shortest=0[ov]")
        last = "ov"
    chain.append(f"[{last}]fps={cfg.fps},format=yuv420p[v]")
    maps = ["-map", "[v]"]
    if len(ai) == 2:
        chain.append(f"[{ai[0]}:a][{ai[1]}:a]amix=inputs=2:duration=longest:dropout_transition=0,"
                     f"aresample=44100:async=1000[a]")
        maps += ["-map", "[a]"]
    elif len(ai) == 1:
        chain.append(f"[{ai[0]}:a]aresample=44100:async=1000[a]")
        maps += ["-map", "[a]"]
    preset, crf = ("ultrafast", "26") if cfg.quality == "light" else ("veryfast", "23")
    enc = ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p", "-g", str(cfg.fps * 2)]
    if ai:
        enc += ["-c:a", "aac", "-b:a", "128k", "-ar", "44100"]
    return [ff, "-y", "-hide_banner", "-loglevel", "warning", *inputs, "-filter_complex", ";".join(chain),
            *maps, *enc, "-f", "matroska", seg_path]


# --------------------------------------------------------------------------- the recorder
class RecorderError(Exception):
    pass


def fmt_time(sec: float) -> str:
    s = int(sec)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Recorder:
    def __init__(self, cfg: RecordConfig):
        self.cfg = cfg
        self.state = "idle"            # idle | recording | paused | stopped
        self.parts_dir = os.path.splitext(cfg.out_path)[0] + ".parts"
        self.segments: List[str] = []
        self.markers: List[Tuple[float, str]] = []
        self._proc: Optional[subprocess.Popen] = None
        self._log = None
        self._seg_start = 0.0
        self._done = 0.0               # recorded seconds in finished segments
        self.ff = find_tool("ffmpeg")

    # ---- timing
    def elapsed(self) -> float:
        run = (time.monotonic() - self._seg_start) if self.state == "recording" else 0.0
        return self._done + run

    # ---- segments
    def _start_segment(self):
        seg = os.path.join(self.parts_dir, f"part{len(self.segments) + 1:03d}.mkv")
        cmd = build_segment_cmd(self.cfg, seg, self.ff)
        self._log = open(os.path.join(self.parts_dir, "ffmpeg.log"), "ab")
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._log,
                                      creationflags=no_window_flags())
        self.segments.append(seg)
        self._seg_start = time.monotonic()

    def _stop_segment(self):
        p = self._proc
        if not p:
            return
        self._done += time.monotonic() - self._seg_start
        try:
            if p.poll() is None:
                try:
                    p.stdin.write(b"q\n")
                    p.stdin.flush()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=5)
        finally:
            try:
                p.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            if self._log:
                self._log.close()
            self._proc = None

    def alive_error(self) -> Optional[str]:
        """If ffmpeg died while we think we are recording, return the reason (last log lines)."""
        if self.state == "recording" and self._proc and self._proc.poll() is not None:
            return self.log_tail() or "The recorder stopped unexpectedly."
        return None

    def log_tail(self, n=6) -> str:
        try:
            with open(os.path.join(self.parts_dir, "ffmpeg.log"), "rb") as f:
                lines = f.read().decode("utf-8", "replace").strip().splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return ""

    # ---- public controls
    def start(self):
        if self.state != "idle":
            raise RecorderError("Already started.")
        folder = os.path.dirname(os.path.abspath(self.cfg.out_path))
        os.makedirs(self.parts_dir, exist_ok=True)
        free = shutil.disk_usage(folder).free
        if free < 1_000_000_000:
            raise RecorderError(f"Only {free / 1e9:.1f} GB free on this drive - free up space first.")
        self._start_segment()
        self.state = "recording"
        time.sleep(0.6)   # let ffmpeg open its inputs; catches bad device names immediately
        err = self.alive_error()
        if err:
            self.state = "stopped"
            raise RecorderError(err)

    def pause(self):
        if self.state == "recording":
            self._stop_segment()
            self.state = "paused"

    def resume(self):
        if self.state == "paused":
            self.state = "recording"
            self._start_segment()

    def mark(self, label: str = "") -> float:
        t = self.elapsed()
        self.markers.append((t, label.strip() or f"Chapter {len(self.markers) + 1}"))
        return t

    def stop(self) -> dict:
        if self.state in ("recording", "paused"):
            self._stop_segment()
        self.state = "stopped"
        return finalize_parts(self.parts_dir, self.cfg.out_path, self.markers)


# --------------------------------------------------------------------------- finishing / recovery
def _valid_segments(parts_dir: str) -> List[str]:
    segs = sorted(f for f in os.listdir(parts_dir) if f.lower().endswith(".mkv"))
    ok = []
    ff = find_tool("ffmpeg")
    for f in segs:
        p = os.path.join(parts_dir, f)
        if os.path.getsize(p) < 2048:
            continue
        r = subprocess.run([ff, "-v", "error", "-i", p, "-f", "null", "-t", "0.1", "-"], capture_output=True,
                           creationflags=no_window_flags())
        if r.returncode == 0:
            ok.append(p)
    return ok


def write_chapters(path: str, markers: List[Tuple[float, str]]) -> Optional[str]:
    if not markers:
        return None
    lines = ["00:00 Start"] + [f"{fmt_time(t)} {name}" for t, name in sorted(markers) if t >= 1.0]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def finalize_parts(parts_dir: str, out_path: str, markers=None, keep_parts: bool = False) -> dict:
    """Join the segments into the final MP4 (stream copy, no quality loss) and write chapters."""
    segs = _valid_segments(parts_dir)
    if not segs:
        raise RecorderError("Nothing was recorded (no usable data). " + _tail(parts_dir))
    ff = find_tool("ffmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    lst = os.path.join(parts_dir, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for s in segs:
            f.write("file '" + s.replace("\\", "/").replace("'", "'\\''") + "'\n")
    r = subprocess.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                        "-movflags", "+faststart", out_path], capture_output=True, text=True,
                       creationflags=no_window_flags())
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RecorderError("Could not join the recording: " + r.stderr[-300:])
    chapters = write_chapters(os.path.splitext(out_path)[0] + ".chapters.txt", markers or [])
    if not keep_parts:
        shutil.rmtree(parts_dir, ignore_errors=True)
    return {"path": out_path, "segments": len(segs), "chapters": chapters}


def _tail(parts_dir):
    try:
        with open(os.path.join(parts_dir, "ffmpeg.log"), "rb") as f:
            return f.read().decode("utf-8", "replace").strip()[-300:]
    except OSError:
        return ""


def recover_parts(parts_dir: str, out_path: Optional[str] = None) -> dict:
    """Rebuild a video from a leftover ``*.parts`` folder (after a crash / power cut)."""
    parts_dir = parts_dir.rstrip("/\\")
    if out_path is None:
        out_path = parts_dir[:-len(".parts")] + " (recovered).mp4" if parts_dir.endswith(".parts") else parts_dir + ".mp4"
    return finalize_parts(parts_dir, out_path)


def clean_voice_cmd(src: str, dst: str, ff: Optional[str] = None) -> List[str]:
    """Voice cleanup pass for a finished lecture: video stream copied, audio denoised + levelled."""
    ff = ff or find_tool("ffmpeg")
    af = "highpass=f=80,afftdn=nr=12:nf=-30:tn=1,acompressor=threshold=-18dB:ratio=3:attack=20:release=250,loudnorm=I=-16:TP=-1.5:LRA=11"
    return [ff, "-y", "-nostdin", "-progress", "pipe:1", "-nostats", "-i", src, "-map", "0:v", "-map", "0:a?",
            "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", dst]


def analyze_audio(path: str) -> Optional[dict]:
    """Loudness check of a recording: {'mean_db', 'max_db'} or None when there is no audio stream."""
    ff = find_tool("ffmpeg")
    r = subprocess.run([ff, "-hide_banner", "-nostdin", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace", creationflags=no_window_flags())
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    mx = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
    if not mean or not mx:
        return None
    return {"mean_db": float(mean.group(1)), "max_db": float(mx.group(1))}


def describe_audio(a: Optional[dict]) -> str:
    if a is None:
        return "No audio was recorded - choose a microphone."
    if a["max_db"] < -55:
        return "Microphone is silent - check the right microphone is selected and not muted."
    if a["max_db"] > -1.5:
        return "Audio is too loud and may distort - move back or lower the microphone level."
    if a["mean_db"] < -40:
        return f"Audio is quiet ({a['mean_db']:.0f} dB) - speak closer or raise the microphone level."
    return f"Audio level is good ({a['mean_db']:.0f} dB average)."
