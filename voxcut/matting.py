"""AI background replacement (Robust Video Matting, ONNX, CPU, fully offline).

Pipeline:  ffmpeg decode -> RVM matte -> composite over the new background -> ffmpeg encode
The result keeps the original audio and is then fed through the normal enhance pipeline.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Callable, Optional

from .paths import find_tool, model_path, no_window_flags

MAT_MODES = ["Off", "Image", "Video", "Blur my room", "Solid colour"]
MAT_QUALITY = {"Fast (720p)": 1280, "High (1080p)": 1920}


def available() -> tuple[bool, str]:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return False, f"AI runtime missing ({e})"
    if not os.path.isfile(model_path()):
        return False, "AI model file missing (models/rvm_mobilenetv3_fp32.onnx)"
    return True, ""


def work_size(w: int, h: int, long_side: int) -> tuple[int, int]:
    """Largest even size <= long_side (long edge) that keeps the aspect ratio; never upscales."""
    scale = min(1.0, long_side / max(w, h))
    ww, hh = int(round(w * scale)), int(round(h * scale))
    return ww - ww % 2, hh - hh % 2


def _hex_to_rgb(c: str):
    c = c.strip().replace("0x", "").lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


class MattingJob:
    def __init__(self, src: str, out: str, mat_mode: str, mat_path: Optional[str], mat_color: str,
                 edge: int, match_light: bool, size: tuple[int, int], fps: int, start: float, length: float):
        self.src, self.out, self.mode, self.bg_path, self.color = src, out, mat_mode, mat_path, mat_color
        self.edge, self.match_light, self.size, self.fps = edge, match_light, size, fps
        self.start, self.length = start, length
        self.procs: list[subprocess.Popen] = []
        self.cancelled = False

    # ---- helpers
    def _popen(self, cmd, **kw):
        p = subprocess.Popen(cmd, creationflags=no_window_flags(), **kw)
        self.procs.append(p)
        return p

    def _rawdec(self, args, W, H):
        cmd = [find_tool("ffmpeg"), "-v", "error"] + args + ["-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
        return self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    @staticmethod
    def _read(proc, n):
        buf = proc.stdout.read(n)
        while buf is not None and len(buf) < n:
            more = proc.stdout.read(n - len(buf))
            if not more:
                break
            buf += more
        return buf

    def _bg_provider(self, np, W, H):
        fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
        n = W * H * 3
        if self.mode == "Solid colour":
            const = np.empty((H, W, 3), np.float32)
            const[:] = _hex_to_rgb(self.color)
            return lambda: const
        if self.mode == "Image":
            p = self._rawdec(["-i", self.bg_path, "-vf", fit, "-frames:v", "1"], W, H)
            data = self._read(p, n)
            if len(data) < n:
                raise RuntimeError("Could not read the background image.")
            img = np.frombuffer(data, np.uint8).reshape(H, W, 3).astype(np.float32)
            return lambda: img
        if self.mode == "Video":
            p = self._rawdec(["-stream_loop", "-1", "-i", self.bg_path, "-vf", f"fps={self.fps},{fit}"], W, H)
        else:  # Blur my room: a blurred copy of the same footage
            small = f"scale={max(2, W // 4)}:{max(2, H // 4)},gblur=sigma=5,scale={W}:{H}:flags=bilinear"
            p = self._rawdec(["-ss", f"{self.start:.3f}", "-t", f"{self.length:.3f}", "-i", self.src,
                              "-vf", f"fps={self.fps},scale={W}:{H},{small}"], W, H)
        last = {"f": None}

        def nxt():
            d = self._read(p, n)
            if len(d) == n:
                last["f"] = np.frombuffer(d, np.uint8).reshape(H, W, 3).astype(np.float32)
            if last["f"] is None:
                raise RuntimeError("Could not read the background video.")
            return last["f"]
        return nxt

    # ---- main loop
    def run(self, on_progress: Callable[[float], None] = lambda p: None) -> None:
        import numpy as np
        import onnxruntime as ort

        W, H = self.size
        n = W * H * 3
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
        sess = ort.InferenceSession(model_path(), so, providers=["CPUExecutionProvider"])
        ratio = np.array([min(1.0, 512.0 / max(W, H))], np.float32)
        rec = [np.zeros((1, 1, 1, 1), np.float32) for _ in range(4)]
        k = 1.0 + self.edge / 50.0

        bg_next = self._bg_provider(np, W, H)
        dec = self._rawdec(["-ss", f"{self.start:.3f}", "-t", f"{self.length:.3f}", "-i", self.src,
                            "-vf", f"fps={self.fps},scale={W}:{H}"], W, H)
        errlog = tempfile.TemporaryFile()
        enc = self._popen(
            [find_tool("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
             "-r", str(self.fps), "-i", "-", "-ss", f"{self.start:.3f}", "-t", f"{self.length:.3f}", "-i", self.src,
             "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", self.out],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errlog)
        total = max(1, int(self.length * self.fps))
        i = 0
        try:
            while not self.cancelled:
                buf = self._read(dec, n)
                if len(buf) < n:
                    break
                frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
                src = (frame.astype(np.float32) * (1 / 255.0)).transpose(2, 0, 1)[None]
                fgr, pha, *rec = sess.run(None, {"src": src, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2],
                                                 "r4i": rec[3], "downsample_ratio": ratio})
                a = pha[0, 0]
                if k > 1.0:  # sharpen the edge of the matte (removes halo / ghosting)
                    a = np.clip((a - 0.5) * k + 0.5, 0.0, 1.0)
                fg = fgr[0].transpose(1, 2, 0) * 255.0
                bg = bg_next()
                if self.match_light:  # nudge person brightness toward the new scene
                    fm, bm = float(fg[::8, ::8].mean()) + 1.0, float(bg[::8, ::8].mean()) + 1.0
                    fg = fg * float(np.clip((bm / fm) ** 0.3, 0.85, 1.2))
                al = a[..., None]
                comp = fg * al + bg * (1.0 - al)
                enc.stdin.write(np.clip(comp, 0, 255).astype(np.uint8).tobytes())
                i += 1
                if i % 3 == 0:
                    on_progress(min(99.0, i / total * 100))
            enc.stdin.close()
            enc.wait()
        except (BrokenPipeError, OSError):
            if not self.cancelled:
                errlog.seek(0)
                raise RuntimeError("Background replacement failed:\n" + errlog.read().decode("utf-8", "replace")[-600:])
        finally:
            for p in self.procs:
                if p.poll() is None:
                    p.kill()
        if self.cancelled:
            raise RuntimeError("Cancelled")
        if i == 0:
            raise RuntimeError("No frames could be read from this video.")
        if enc.returncode not in (0, None):
            errlog.seek(0)
            raise RuntimeError("Encoding failed:\n" + errlog.read().decode("utf-8", "replace")[-600:])
        on_progress(100.0)

    def cancel(self):
        self.cancelled = True
        for p in self.procs:
            if p.poll() is None:
                p.kill()
