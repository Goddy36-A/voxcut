"""Builds and runs ffmpeg commands. No GUI code here so it can be unit-tested."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from .paths import find_tool, no_window_flags

ORIENTATIONS = {  # name -> aspect (w, h); None = keep source
    "Keep original": None,
    "Landscape 16:9": (16, 9),
    "Portrait 9:16": (9, 16),
    "Square 1:1": (1, 1),
    "Portrait 4:5": (4, 5),
}
# target "short side" in pixels
QUALITIES = {"480p": 480, "720p (HD)": 720, "1080p (Full HD)": 1080,
             "1440p (2K)": 1440, "2160p (4K)": 2160}
FIT_MODES = ["Blurred background", "Solid colour background", "Image background", "Crop to fill"]
COMPRESSION = {  # name -> (crf, preset)
    "Maximum quality (big file)": (18, "slow"),
    "Balanced": (23, "medium"),
    "Small file": (28, "medium"),
    "Smallest file": (32, "fast"),
}
CODECS = {"H.264 (most compatible)": "libx264", "H.265 / HEVC (smaller)": "libx265"}


@dataclass
class Settings:
    # audio
    noise_removal: bool = True
    noise_strength: int = 12          # dB reduction, 5..30
    voice_boost: bool = True
    compressor: bool = True
    normalize: bool = True
    target_lufs: float = -14.0
    extra_gain_db: float = 0.0
    # video
    orientation: str = "Keep original"
    quality: str = "1080p (Full HD)"
    fit_mode: str = "Blurred background"
    bg_color: str = "black"
    bg_image: Optional[str] = None
    fps: int = 30
    compression: str = "Balanced"
    codec: str = "H.264 (most compatible)"
    # extras
    intro: Optional[str] = None
    outro: Optional[str] = None
    audio_only_enhance: bool = False   # copy video untouched, only fix the audio


def probe(path: str) -> dict:
    out = subprocess.run(
        [find_tool("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, creationflags=no_window_flags())
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def media_info(path: str) -> dict:
    d = probe(path)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    dur = float(d["format"].get("duration", 0) or 0)
    w = h = 0
    if v:
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
        if abs(rot) in (90, 270):   # ffmpeg auto-rotates, so swap dimensions
            w, h = h, w
    return {"has_video": v is not None, "has_audio": a is not None, "w": w, "h": h, "duration": dur}


def target_size(info: dict, s: Settings) -> tuple[int, int]:
    short = QUALITIES[s.quality]
    asp = ORIENTATIONS[s.orientation]
    if asp is None:
        w, h = info["w"], info["h"]
        if w == 0:
            return 1920, 1080
        ow, oh = (round(short * w / h), short) if w >= h else (short, round(short * h / w))
    else:
        aw, ah = asp
        ow, oh = (round(short * aw / ah), short) if aw >= ah else (short, round(short * ah / aw))
    return ow - ow % 2, oh - oh % 2


def audio_filter(s: Settings, enhance: bool) -> str:
    f = []
    if enhance:
        if s.noise_removal:
            f += ["highpass=f=80", f"afftdn=nr={s.noise_strength}:nf=-30:tn=1"]
        if s.voice_boost:
            f += ["equalizer=f=120:t=q:w=1:g=2",        # warmth
                  "equalizer=f=300:t=q:w=1:g=-2",       # less boxiness
                  "equalizer=f=3200:t=q:w=1:g=3",       # presence / clarity
                  "equalizer=f=9000:t=h:w=4000:g=1.5"]  # air
        if s.compressor:
            f.append("acompressor=threshold=-20dB:ratio=3:attack=5:release=120:makeup=3")
        if s.extra_gain_db:
            f.append(f"volume={s.extra_gain_db}dB")
        if s.normalize:
            f.append(f"loudnorm=I={s.target_lufs}:TP=-1.5:LRA=11")
        f.append("alimiter=limit=0.95")
    f.append("aresample=48000")
    f.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
    return ",".join(f)


def build_command(main: str, out: str, s: Settings) -> tuple[list[str], float]:
    """Returns (ffmpeg argv, expected total duration in seconds)."""
    ffmpeg = find_tool("ffmpeg")
    minfo = media_info(main)
    if not minfo["has_video"]:
        raise RuntimeError("Input has no video stream.")
    head = [ffmpeg, "-hide_banner", "-nostats", "-progress", "pipe:1", "-y"]

    if s.audio_only_enhance and not (s.intro or s.outro):
        cmd = head + ["-i", main, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy",
                      "-af", audio_filter(s, True), "-c:a", "aac", "-b:a", "192k", out]
        return cmd, minfo["duration"]

    W, H = target_size(minfo, s)
    segs = []
    if s.intro:
        segs.append((s.intro, False))
    segs.append((main, True))
    if s.outro:
        segs.append((s.outro, False))

    inputs: list[str] = []
    filters: list[str] = []
    labels = []
    total = 0.0
    idx = 0
    for si, (path, is_main) in enumerate(segs):
        info = minfo if is_main else media_info(path)
        total += info["duration"]
        vin = idx
        inputs += ["-i", path]
        idx += 1
        base = f"[{vin}:v]fps={s.fps},setsar=1"
        vl = f"v{si}"
        if s.fit_mode == "Crop to fill":
            filters.append(f"{base},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                           f"crop={W}:{H},format=yuv420p[{vl}]")
        elif s.fit_mode == "Solid colour background":
            filters.append(f"{base},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                           f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={s.bg_color},format=yuv420p[{vl}]")
        elif s.fit_mode == "Image background" and s.bg_image:
            img = idx
            inputs += ["-loop", "1", "-i", s.bg_image]
            idx += 1
            filters.append(f"{base},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg{si}]")
            filters.append(f"[{img}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                           f"fps={s.fps},setsar=1[bg{si}]")
            filters.append(f"[bg{si}][fg{si}]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[{vl}]")
        else:  # blurred background
            filters.append(f"{base},split[a{si}][b{si}]")
            filters.append(f"[a{si}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                           f"boxblur=25:5,eq=brightness=-0.08[bg{si}]")
            filters.append(f"[b{si}]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg{si}]")
            filters.append(f"[bg{si}][fg{si}]overlay=(W-w)/2:(H-h)/2,format=yuv420p[{vl}]")
        al = f"a{si}o"
        if info["has_audio"]:
            filters.append(f"[{vin}:a]{audio_filter(s, is_main)}[{al}]")
        else:  # silent track so concat never breaks
            dur = max(info["duration"], 0.1)
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f},asetpts=N/SR/TB[{al}]")
        labels.append(f"[{vl}][{al}]")

    if len(segs) > 1:
        filters.append("".join(labels) + f"concat=n={len(segs)}:v=1:a=1[vout][aout]")
        vmap, amap = "[vout]", "[aout]"
    else:
        vmap, amap = "[v0]", "[a0o]"

    crf, preset = COMPRESSION[s.compression]
    codec = CODECS[s.codec]
    venc = ["-c:v", codec, "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p"]
    if codec == "libx265":
        venc += ["-tag:v", "hvc1"]
    cmd = (head + inputs + ["-filter_complex", ";".join(filters), "-map", vmap, "-map", amap]
           + venc + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
    return cmd, total


class Job:
    """Runs one ffmpeg process, reporting progress 0..100."""

    def __init__(self, cmd: list[str], duration: float):
        self.cmd, self.duration = cmd, max(duration, 0.1)
        self.proc: Optional[subprocess.Popen] = None
        self.cancelled = False

    def run(self, on_progress: Callable[[float], None] = lambda p: None) -> None:
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, creationflags=no_window_flags())
        tail: list[str] = []
        for line in self.proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if line.startswith("out_time_us="):
                try:
                    on_progress(min(99.0, int(line.split("=")[1]) / 1_000_000 / self.duration * 100))
                except ValueError:
                    pass
            elif "=" not in line:
                tail = (tail + [line])[-15:]
        self.proc.wait()
        if self.cancelled:
            raise RuntimeError("Cancelled")
        if self.proc.returncode != 0:
            raise RuntimeError("ffmpeg failed:\n" + "\n".join(tail))
        on_progress(100.0)

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
