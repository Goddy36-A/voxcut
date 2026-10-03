"""Builds a finished video from a text prompt.

Online (needs API key): the plan (compose), narration (tts), pictures/clips and music (media).
On this PC (FFmpeg): every scene is rendered, joined, and the music mixed in.

Flow
  1. compose(prompt)            -> plan with scenes: text + visual search phrase, voice, music query
  2. per scene: tts, media search + download, caption image, render scene clip
  3. join scene clips           -> one MP4
  4. mix in background music    -> final MP4 (+ <name>.credits.txt for the media sources)

No Qt imports at module level, so the pipeline is unit-testable; caption drawing is injected.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Callable, Optional

from .cloud import CloudClient, CloudError
from .engine import Job, media_info
from .paths import find_tool, no_window_flags  # noqa: F401  (no_window_flags used by Job)

# name -> (width, height, orientation word for media search or None)
FORMATS = {
    "Portrait 9:16 (Reels / TikTok / Shorts)": (1080, 1920, "vertical"),
    "Landscape 16:9 (YouTube)": (1920, 1080, "horizontal"),
    "Square 1:1": (1080, 1080, None),
}
FPS = 30
MIN_SCENE = 3.5          # seconds, even for very short text
NARRATION_PAD = 0.9      # silence after the voice finishes
VOICE_FALLBACK = "alloy"


class Cancelled(Exception):
    pass


def _noop(*_a, **_k):
    pass


# --------------------------------------------------------------------------- captions (Qt)
def render_caption_qt(text: str, author: str, w: int, h: int, path: str) -> None:
    """Draw the scene text on a transparent PNG (white text on a soft dark panel)."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter

    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    margin = int(w * 0.09)
    max_w = w - 2 * margin
    max_h = int(h * 0.55)
    flags = int(Qt.AlignCenter | Qt.TextWordWrap)
    size = max(18, int(w * 0.055))
    while True:
        font = QFont("Segoe UI")
        font.setPixelSize(size)
        font.setWeight(QFont.DemiBold)
        fm = QFontMetrics(font)
        box = fm.boundingRect(0, 0, max_w, 10_000, flags, text)
        if box.height() <= max_h or size <= 18:
            break
        size -= 2
    a_font = QFont("Segoe UI")
    a_font.setPixelSize(max(16, int(size * 0.62)))
    a_font.setWeight(QFont.Medium)
    a_h = QFontMetrics(a_font).height() + 14 if author else 0
    pad = int(size * 0.7)
    panel_h = box.height() + a_h + 2 * pad
    panel_w = min(w - margin, box.width() + 2 * pad)
    px, py = (w - panel_w) / 2, (h - panel_h) / 2
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 120))
    p.drawRoundedRect(QRectF(px, py, panel_w, panel_h), pad * 0.6, pad * 0.6)
    p.setFont(font)
    shadow = QRectF(margin + 2, py + pad + 2, max_w, box.height())
    p.setPen(QColor(0, 0, 0, 160))
    p.drawText(shadow, flags, text)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(margin, py + pad, max_w, box.height()), flags, text)
    if author:
        p.setFont(a_font)
        p.setPen(QColor(255, 214, 140))
        p.drawText(QRectF(margin, py + pad + box.height() + 8, max_w, a_h), int(Qt.AlignHCenter | Qt.AlignTop), author)
    p.end()
    img.save(path, "PNG")


# --------------------------------------------------------------------------- media fetching
def fetch_media(cloud: CloudClient, query: str, kinds, orientation: Optional[str], dest_no_ext: str):
    """Search and download the first usable result. Returns (path, kind, credit) or None."""
    for kind in kinds:
        for orient in ([orientation, None] if orientation else [None]):
            try:
                items = cloud.media_items(kind, query, orientation=orient)
            except CloudError:
                continue  # e.g. orientation value not accepted -> retry without it
            for it in items[:4]:
                urls = [u for u in (it.get("url"), it.get("thumb") if kind == "image" else None) if u]
                for u in urls:
                    try:
                        return cloud.download(u, dest_no_ext), kind, it.get("credit") or ""
                    except CloudError:
                        continue
            if items:
                break
    return None


# --------------------------------------------------------------------------- ffmpeg commands
def _scene_cmd(ff, bg, bg_kind, caption_png, narration, dur, w, h, out):
    n = max(1, int(dur * FPS))
    inputs, fidx = [], 0
    if bg_kind == "video":
        inputs += ["-stream_loop", "-1", "-i", bg]
        vf = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},setsar=1,"
              f"eq=brightness=-0.08:saturation=1.05[bg]")
    else:
        inputs += ["-i", bg]
        big_w, big_h = int(w * 1.3), int(h * 1.3)
        vf = (f"[0:v]scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
              f"zoompan=z='1+0.12*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={w}x{h}:fps={FPS},"
              f"setsar=1,eq=brightness=-0.08:saturation=1.05[bg]")
    inputs += ["-loop", "1", "-framerate", str(FPS), "-i", caption_png]
    if narration:
        inputs += ["-i", narration]
        af = "[2:a]adelay=300|300,aresample=44100,aformat=channel_layouts=stereo,apad[a]"
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        af = "[2:a]aresample=44100,aformat=channel_layouts=stereo[a]"
    fade = f"fade=t=in:st=0:d=0.5,fade=t=out:st={max(dur - 0.5, 0.1):.2f}:d=0.5"
    fc = (f"{vf};[bg][1:v]overlay=0:0:format=auto,{fade},format=yuv420p[v];{af}")
    return [ff, "-y", "-nostdin", "-progress", "pipe:1", "-nostats", *inputs, "-filter_complex", fc,
            "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", out]


def _concat(ff, clips, out, workdir):
    lst = os.path.join(workdir, "scenes.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for c in clips:
            f.write("file '" + c.replace("\\", "/").replace("'", "'\\''") + "'\n")
    return [ff, "-y", "-nostdin", "-progress", "pipe:1", "-nostats", "-f", "concat", "-safe", "0", "-i", lst,
            "-c", "copy", "-movflags", "+faststart", out]


def _mix_music(ff, video, music, total, out, volume=0.12):
    fc = (f"[1:a]volume={volume},afade=t=in:st=0:d=1.5,afade=t=out:st={max(total - 2.5, 0):.2f}:d=2.5[m];"
          f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0,volume=1.8,alimiter=limit=0.95[a]")
    return [ff, "-y", "-nostdin", "-progress", "pipe:1", "-nostats", "-i", video, "-stream_loop", "-1", "-i", music,
            "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", out]


# --------------------------------------------------------------------------- the pipeline
def make_video(cloud: CloudClient, prompt: str, out_path: str, fmt: str = "Portrait 9:16 (Reels / TikTok / Shorts)",
               narrate: Optional[bool] = None, music: bool = True,
               on_status: Callable[[str], None] = _noop, on_progress: Callable[[float], None] = _noop,
               should_cancel: Callable[[], bool] = lambda: False,
               render_caption: Callable = render_caption_qt, plan: Optional[dict] = None,
               keep_workdir: bool = False) -> dict:
    """Returns {"path", "duration", "scenes", "credits": [...], "notes": [...]}. Raises Cancelled / CloudError."""
    w, h, orientation = FORMATS[fmt]
    ff = find_tool("ffmpeg")
    notes: list[str] = []
    credits: list[str] = []
    work = tempfile.mkdtemp(prefix="voxcut_ai_")
    current: dict = {"job": None}

    def check():
        if should_cancel():
            if current["job"]:
                current["job"].cancel()
            raise Cancelled()

    def run(cmd, dur, lo, hi):
        check()
        job = Job(cmd, dur)
        current["job"] = job
        try:
            job.run(lambda p: on_progress(lo + (hi - lo) * p / 100.0))
        except RuntimeError:
            if should_cancel():
                raise Cancelled()
            raise
        finally:
            current["job"] = None

    try:
        on_status("Planning the video...")
        plan = plan or cloud.compose(prompt)
        items = [it for it in (plan.get("items") or []) if (it.get("text") or "").strip()]
        if not items:
            raise CloudError("The plan had no scenes. Try a more specific prompt.")
        do_narr = bool(plan.get("narrate", True)) if narrate is None else narrate
        voice = plan.get("voice") or VOICE_FALLBACK
        bg_kind = plan.get("backgroundKind") if plan.get("backgroundKind") in ("image", "video") else "image"
        kinds = [bg_kind, "image" if bg_kind == "video" else "video"]
        per = float(plan.get("perSlideSec") or 6)
        n = len(items)
        clips: list[str] = []
        total = 0.0
        scene_share = 85.0 / n
        for i, it in enumerate(items):
            check()
            base = i * scene_share
            tag = f"Scene {i + 1}/{n}"
            narration = None
            dur = max(per, MIN_SCENE)
            if do_narr:
                on_status(f"{tag}: recording narration...")
                try:
                    try:
                        mp3 = cloud.tts(it["text"], voice)
                    except CloudError:
                        if voice == VOICE_FALLBACK:
                            raise
                        mp3 = cloud.tts(it["text"], VOICE_FALLBACK)  # plan voice not accepted
                    narration = os.path.join(work, f"s{i}.mp3")
                    with open(narration, "wb") as f:
                        f.write(mp3)
                    dur = max(media_info(narration)["duration"] + NARRATION_PAD, MIN_SCENE)
                except CloudError as e:
                    notes.append(f"{tag}: narration skipped ({e})")
                    narration = None
            check()
            on_status(f"{tag}: finding visuals...")
            queries = [q for q in (it.get("visual"), plan.get("backgroundQuery"), it.get("text", "")[:40]) if q]
            got = None
            for q in queries:
                got = fetch_media(cloud, q, kinds, orientation, os.path.join(work, f"bg{i}"))
                if got:
                    break
            if not got:
                raise CloudError(f"{tag}: no picture or clip found for this scene.")
            bg, kind, credit = got
            if credit:
                credits.append(credit)
            cap = os.path.join(work, f"cap{i}.png")
            render_caption(it["text"].strip(), (it.get("author") or "").strip(), w, h, cap)
            clip = os.path.join(work, f"scene{i}.mp4")
            on_status(f"{tag}: rendering...")
            run(_scene_cmd(ff, bg, kind, cap, narration, dur, w, h, clip), dur, base, base + scene_share)
            clips.append(clip)
            total += dur

        on_status("Joining scenes...")
        joined = os.path.join(work, "joined.mp4")
        run(_concat(ff, clips, joined, work), total, 85, 90)

        final = joined
        mq = plan.get("musicQuery")
        if music and mq:
            on_status("Adding background music...")
            got = fetch_media(cloud, mq, ["music"], None, os.path.join(work, "music"))
            if got:
                mixed = os.path.join(work, "mixed.mp4")
                try:
                    run(_mix_music(ff, joined, got[0], total, mixed), total, 90, 99)
                    final = mixed
                    if got[2]:
                        credits.append(got[2])
                except RuntimeError as e:
                    notes.append(f"Music skipped: {str(e).splitlines()[-1] if str(e) else 'mix failed'}")
            else:
                notes.append("Music skipped: nothing found for this style.")

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        shutil.copyfile(final, out_path)
        if credits:
            with open(os.path.splitext(out_path)[0] + ".credits.txt", "w", encoding="utf-8") as f:
                f.write("Media credits\n\n" + "\n".join(sorted(set(credits))) + "\n")
        on_progress(100.0)
        on_status("Done")
        return {"path": out_path, "duration": total, "scenes": n, "credits": sorted(set(credits)), "notes": notes}
    finally:
        if not keep_workdir:
            shutil.rmtree(work, ignore_errors=True)
