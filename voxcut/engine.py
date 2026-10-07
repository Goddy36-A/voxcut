"""Builds and runs ffmpeg commands. No GUI code here so it can be unit-tested."""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import tempfile
import uuid
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

COLOR_PRESETS = {
    "None": "",
    "Cinematic (teal & orange)": "colorbalance=rs=-0.08:bs=0.08:rh=0.08:bh=-0.08,eq=contrast=1.08:saturation=1.15",
    "Vibrant": "eq=saturation=1.4:contrast=1.08",
    "Warm": "colorbalance=rs=0.08:bs=-0.08:rm=0.06:bm=-0.06",
    "Cool": "colorbalance=rs=-0.06:bs=0.08:rm=-0.05:bm=0.06",
    "Black & white": "hue=s=0,eq=contrast=1.1",
    "Vintage": "curves=preset=vintage",
    "Sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
    "Bright & clean": "eq=brightness=0.05:contrast=1.05:saturation=1.1",
}
WM_POSITIONS = {
    "Top left": "24:24", "Top right": "W-w-24:24", "Bottom left": "24:H-h-24",
    "Bottom right": "W-w-24:H-h-24", "Center": "(W-w)/2:(H-h)/2",
}
AUDIO_FORMATS = {"MP3": ("mp3", ["-c:a", "libmp3lame", "-q:a", "2"]),
                 "WAV (lossless)": ("wav", ["-c:a", "pcm_s16le"]),
                 "M4A (AAC)": ("m4a", ["-c:a", "aac", "-b:a", "192k"])}


@dataclass
class Settings:
    # ---- audio / voice
    noise_removal: bool = True
    noise_strength: int = 12          # dB, 5..30
    voice_boost: bool = True
    compressor: bool = True
    normalize: bool = True
    target_lufs: float = -14.0
    extra_gain_db: float = 0.0
    audio_only_enhance: bool = False  # copy video untouched, only fix the audio
    # ---- format
    orientation: str = "Keep original"
    quality: str = "1080p (Full HD)"
    fit_mode: str = "Blurred background"
    bg_color: str = "black"
    bg_image: Optional[str] = None
    fps: int = 30
    compression: str = "Balanced"
    codec: str = "H.264 (most compatible)"
    # ---- effects
    color_preset: str = "None"
    brightness: int = 0               # -50..50
    contrast: int = 0
    saturation: int = 0
    gamma: int = 0
    warmth: int = 0
    sharpen: int = 0                  # 0..100
    vignette: bool = False
    video_denoise: bool = False
    mirror: bool = False
    fade_in: float = 0.0
    fade_out: float = 0.0
    speed: float = 1.0                # 0.5..2.0
    trim_start: float = 0.0
    trim_end: float = 0.0             # 0 = until the end
    # ---- logo / watermark
    watermark: Optional[str] = None
    wm_position: str = "Bottom right"
    wm_size: int = 15                 # % of video width
    wm_opacity: int = 80
    # ---- background music
    music: Optional[str] = None
    music_volume: int = 20            # %
    music_duck: bool = True           # lower music while someone speaks
    music_fade_out: float = 2.0
    # ---- intro / outro
    intro: Optional[str] = None
    outro: Optional[str] = None
    # ---- AI background replacement
    mat_mode: str = "Off"             # Off | Image | Video | Blur my room | Solid colour
    mat_path: Optional[str] = None    # background image / video
    mat_color: str = "#00B140"
    mat_edge: int = 20                # 0..100 edge sharpness
    mat_light: bool = True            # match person brightness to the new scene
    mat_quality: str = "Fast (720p)"


# --------------------------------------------------------------------------- probing
def probe(path: str) -> dict:
    out = subprocess.run(
        [find_tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
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
        if abs(rot) in (90, 270):
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


# --------------------------------------------------------------------------- filters
def audio_filter(s: Settings, enhance: bool, speed: float = 1.0) -> str:
    f = []
    if speed != 1.0:
        f.append(f"atempo={speed}")
    if enhance:
        if s.noise_removal:
            f += ["highpass=f=80", f"afftdn=nr={s.noise_strength}:nf=-30:tn=1"]
        if s.voice_boost:
            f += ["equalizer=f=120:t=q:w=1:g=2", "equalizer=f=300:t=q:w=1:g=-2",
                  "equalizer=f=3200:t=q:w=1:g=3", "equalizer=f=9000:t=h:w=4000:g=1.5"]
        if s.compressor:
            f.append("acompressor=threshold=-20dB:ratio=3:attack=5:release=120:makeup=3")
        if s.extra_gain_db:
            f.append(f"volume={s.extra_gain_db}dB")
        if s.normalize:
            f.append(f"loudnorm=I={s.target_lufs}:TP=-1.5:LRA=11")
        f.append("alimiter=limit=0.95")
    f += ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
    return ",".join(f)


def video_effects(s: Settings, dur: float) -> list[str]:
    """Filters applied to the main clip only. `dur` = its length after speed change."""
    f = []
    if s.speed != 1.0:
        f.append(f"setpts=PTS/{s.speed}")
    if s.video_denoise:
        f.append("hqdn3d=3:2:4:3")
    if COLOR_PRESETS.get(s.color_preset):
        f.append(COLOR_PRESETS[s.color_preset])
    eqp = []
    if s.brightness:
        eqp.append(f"brightness={s.brightness / 100:.3f}")
    if s.contrast:
        eqp.append(f"contrast={1 + s.contrast / 50:.3f}")
    if s.saturation:
        eqp.append(f"saturation={1 + s.saturation / 50:.3f}")
    if s.gamma:
        eqp.append(f"gamma={1 + s.gamma / 100:.3f}")
    if eqp:
        f.append("eq=" + ":".join(eqp))
    if s.warmth:
        t = s.warmth / 50 * 0.12
        f.append(f"colorbalance=rs={t:.3f}:rm={t:.3f}:rh={t:.3f}:bs={-t:.3f}:bm={-t:.3f}:bh={-t:.3f}")
    if s.sharpen:
        f.append(f"unsharp=5:5:{s.sharpen / 100 * 1.5:.2f}:5:5:0")
    if s.vignette:
        f.append("vignette=PI/5")
    if s.mirror:
        f.append("hflip")
    if s.fade_in > 0:
        f.append(f"fade=t=in:st=0:d={s.fade_in}")
    if s.fade_out > 0 and dur > s.fade_out:
        f.append(f"fade=t=out:st={dur - s.fade_out:.3f}:d={s.fade_out}")
    return f


def audio_source(idx: int, info: dict, enhance: bool, label: str, s: Settings, speed: float, dur: float) -> str:
    if info["has_audio"]:
        return f"[{idx}:a]{audio_filter(s, enhance, speed)}[{label}]"
    return f"anullsrc=r=48000:cl=stereo,atrim=0:{max(dur, 0.1):.3f},asetpts=N/SR/TB[{label}]"


def music_graph(src_label: str, midx: int, s: Settings, total: float) -> tuple[list[str], str]:
    """Mix looped background music under the final voice track (with optional ducking)."""
    vol = s.music_volume / 100
    chain = f"[{midx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={vol:.3f}"
    if s.music_fade_out > 0 and total > s.music_fade_out:
        chain += f",afade=t=out:st={total - s.music_fade_out:.3f}:d={s.music_fade_out}"
    fl = [chain + "[mus]"]
    if s.music_duck:
        fl.append(f"[{src_label}]asplit=2[vo][sc]")
        fl.append("[mus][sc]sidechaincompress=threshold=0.04:ratio=10:attack=20:release=500[duck]")
        fl.append("[vo][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
    else:
        fl.append(f"[{src_label}][mus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
    fl.append("[mix]alimiter=limit=0.95[mixo]")
    return fl, "mixo"


HEAD = ["-hide_banner", "-nostats", "-progress", "pipe:1", "-y"]


# --------------------------------------------------------------------------- main enhance pipeline
def build_command(main: str, out: str, s: Settings, preview: Optional[tuple[float, float]] = None):
    """Returns (ffmpeg argv, expected output duration in seconds).
    preview=(start, length) renders a small, fast low-res sample of the main clip only."""
    ffmpeg = find_tool("ffmpeg")
    minfo = media_info(main)
    if not minfo["has_video"]:
        raise RuntimeError("Input has no video stream.")
    dur_in = minfo["duration"]
    start = max(0.0, s.trim_start)
    end = s.trim_end if s.trim_end > 0 else dur_in
    end = min(end, dur_in) if dur_in else end
    if preview:
        start = start + preview[0]
        end = min(end, start + preview[1])
        s = dataclasses.replace(s, quality="480p", intro=None, outro=None)
    if end - start < 0.1:
        raise RuntimeError("The trim / preview range is empty. Check Start and End times.")
    speed = s.speed if 0.5 <= s.speed <= 2.0 else 1.0
    s = dataclasses.replace(s, speed=speed)
    main_dur = (end - start) / speed
    in_opts: list[str] = []
    if start > 0:
        in_opts += ["-ss", f"{start:.3f}"]
    if dur_in and end < dur_in - 0.01:
        in_opts += ["-t", f"{end - start:.3f}"]

    # ---- fast path: audio-only fix, video stream copied ----------------------------------
    if s.audio_only_enhance and not (s.intro or s.outro or preview or speed != 1.0 or in_opts):
        inputs = ["-i", main]
        fl = [audio_source(0, minfo, True, "a0", s, 1.0, main_dur)]
        label = "a0"
        if s.music:
            inputs += ["-stream_loop", "-1", "-i", s.music]
            mf, label = music_graph("a0", 1, s, main_dur)
            fl += mf
        cmd = ([ffmpeg] + HEAD + inputs + ["-filter_complex", ";".join(fl), "-map", "0:v:0", "-map", f"[{label}]",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
               + (["-t", f"{main_dur:.3f}"] if s.music else []) + [out])
        return cmd, main_dur

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
        seg_dur = main_dur if is_main else info["duration"]
        total += seg_dur
        vin = idx
        inputs += (in_opts if is_main else []) + ["-i", path]
        idx += 1
        eff = ",".join([f"fps={s.fps}", "setsar=1"] + (video_effects(s, main_dur) if is_main else []))
        base = f"[{vin}:v]{eff}"
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
        filters.append(audio_source(vin, info, is_main, al, s, speed if is_main else 1.0, seg_dur))
        labels.append(f"[{vl}][{al}]")

    if len(segs) > 1:
        filters.append("".join(labels) + f"concat=n={len(segs)}:v=1:a=1[vout][aout]")
        vfin, afin = "vout", "aout"
    else:
        vfin, afin = "v0", "a0o"

    if s.watermark:
        wi = idx
        inputs += ["-i", s.watermark]
        idx += 1
        ww = max(16, int(W * s.wm_size / 100)) // 2 * 2
        filters.append(f"[{wi}:v]scale={ww}:-1,format=rgba,colorchannelmixer=aa={s.wm_opacity / 100:.2f}[wm]")
        filters.append(f"[{vfin}][wm]overlay={WM_POSITIONS[s.wm_position]}[vwm]")
        vfin = "vwm"

    if s.music:
        mi = idx
        inputs += ["-stream_loop", "-1", "-i", s.music]
        idx += 1
        mf, afin = music_graph(afin, mi, s, total)
        filters += mf

    if preview:
        venc = ["-c:v", "libx264", "-crf", "30", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    else:
        crf, preset = COMPRESSION[s.compression]
        codec = CODECS[s.codec]
        venc = ["-c:v", codec, "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p"]
        if codec == "libx265":
            venc += ["-tag:v", "hvc1"]
    cmd = ([ffmpeg] + HEAD + inputs + ["-filter_complex", ";".join(filters), "-map", f"[{vfin}]", "-map", f"[{afin}]"]
           + venc + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
           + (["-t", f"{total:.3f}"] if s.music else []) + [out])
    return cmd, total


# --------------------------------------------------------------------------- separation / addition tools
def plan_extract_audio(src: str, out: str, fmt: str, enhance: bool, s: Settings):
    info = media_info(src)
    if not info["has_audio"]:
        raise RuntimeError("This file has no audio track.")
    _, codec = AUDIO_FORMATS[fmt]
    af = ["-af", audio_filter(s, True)] if enhance else []
    return [find_tool("ffmpeg")] + HEAD + ["-i", src, "-vn"] + af + codec + [out], info["duration"]


def plan_remove_audio(src: str, out: str):
    info = media_info(src)
    return [find_tool("ffmpeg")] + HEAD + ["-i", src, "-map", "0:v:0", "-an", "-c:v", "copy", out], info["duration"]


def plan_add_audio(src: str, audio: str, out: str, mode: str = "replace", volume: int = 100, loop: bool = True):
    """mode: 'replace' or 'mix'. Video is copied untouched (fast, lossless)."""
    info = media_info(src)
    vol = volume / 100
    dur = info["duration"]
    inputs = ["-i", src] + (["-stream_loop", "-1"] if loop else []) + ["-i", audio]
    if mode == "mix" and info["has_audio"]:
        fc = (f"[1:a]volume={vol:.3f},aresample=48000[m];[0:a]aresample=48000[o];"
              "[o][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]")
    else:
        fc = f"[1:a]volume={vol:.3f},aresample=48000[a]"
    cmd = ([find_tool("ffmpeg")] + HEAD + inputs + ["-filter_complex", fc, "-map", "0:v:0", "-map", "[a]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"] + (["-t", f"{dur:.3f}"] if loop else []) + [out])
    return cmd, dur


# --------------------------------------------------------------------------- background replacement chain
class Chain:
    """Runs several steps one after another, mapping their progress into one 0..100 bar."""

    def __init__(self, steps, cleanup: Callable[[], None] = lambda: None):
        self.steps, self.cleanup, self.cur, self.cancelled = steps, cleanup, None, False

    def run(self, on_progress: Callable[[float], None] = lambda p: None) -> None:
        total = sum(w for _, w in self.steps)
        done = 0.0
        try:
            for factory, w in self.steps:
                if self.cancelled:
                    raise RuntimeError("Cancelled")
                self.cur = factory()
                self.cur.run(lambda p, base=done, w=w: on_progress((base + p / 100 * w) / total * 100))
                done += w
            on_progress(100.0)
        finally:
            self.cleanup()

    def cancel(self):
        self.cancelled = True
        if self.cur:
            self.cur.cancel()


def plan_enhance(main: str, out: str, s: Settings, preview: Optional[tuple[float, float]] = None):
    """Returns (runner, out). Plain ffmpeg job normally; with AI background replacement it is a 2-step chain:
    1) cut the person out and place them on the new background, 2) the normal enhance pipeline."""
    if s.mat_mode == "Off":
        cmd, dur = build_command(main, out, s, preview)
        return Job(cmd, dur), out
    from .matting import MAT_QUALITY, MattingJob, available, work_size
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    minfo = media_info(main)
    if not minfo["has_video"]:
        raise RuntimeError("Input has no video stream.")
    dur_in = minfo["duration"]
    start = max(0.0, s.trim_start)
    end = s.trim_end if s.trim_end > 0 else dur_in
    end = min(end, dur_in) if dur_in else end
    if preview:
        start += preview[0]
        end = min(end, start + preview[1])
    if end - start < 0.1:
        raise RuntimeError("The trim / preview range is empty. Check Start and End times.")
    size = work_size(minfo["w"], minfo["h"], MAT_QUALITY[s.mat_quality])
    tmp = os.path.join(tempfile.gettempdir(), f"ideawood_bg_{uuid.uuid4().hex[:8]}.mp4")
    s2 = dataclasses.replace(s, mat_mode="Off", trim_start=0.0, trim_end=0.0)

    def step1():
        return MattingJob(main, tmp, s.mat_mode, s.mat_path, s.mat_color, s.mat_edge, s.mat_light,
                          size, s.fps, start, end - start)

    def step2():
        cmd, dur = build_command(tmp, out, s2, (0.0, 1e9) if preview else None)
        return Job(cmd, dur)

    def cleanup():
        try:
            os.remove(tmp)
        except OSError:
            pass
    return Chain([(step1, 85), (step2, 15)], cleanup), out


# --------------------------------------------------------------------------- runner
class Job:
    """Runs one ffmpeg process, reporting progress 0..100."""

    def __init__(self, cmd: list[str], duration: float, cwd: Optional[str] = None):
        self.cmd, self.duration, self.cwd = cmd, max(duration, 0.1), cwd
        self.proc: Optional[subprocess.Popen] = None
        self.cancelled = False

    def run(self, on_progress: Callable[[float], None] = lambda p: None) -> None:
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", creationflags=no_window_flags(),
                                     cwd=self.cwd)
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
