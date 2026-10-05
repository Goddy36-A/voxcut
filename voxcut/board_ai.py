"""Board AI (the 'DoodleDraw' Lovable app): read handwriting, explain the board, finish sketches.

POST {BASE}/api/public/v1/board   header ``x-api-key``   (this is a DIFFERENT key from the QuoteTube one)
  {"image": "data:image/png;base64,...", "tasks": ["read","explain"], "lens": "socratic", "context": "..."}

Verified from the owner's example: ``result["transcription"]["latex"]`` and ``result["explanation"]["quiz"]``.
Everything else (title/summary/key-points names, the "finish" task name and its shapes) is parsed leniently and is
NOT verified against the live API - see docs/BOARD_AI.md. Unknown replies are always available as raw JSON.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .cloud import USER_AGENT, CloudError, NotSignedIn, _server_message

BASE = (os.environ.get("IDEAWOOD_BOARD_BASE") or "https://dooddraw.lovable.app").rstrip("/")
TASK_READ, TASK_EXPLAIN = "read", "explain"
TASK_FINISH = "finish"            # UNVERIFIED name for "finish sketches"; change here if the API uses another word
LENSES = ["socratic"]             # only 'socratic' is known; the combo box is editable for other lens names
TIMEOUT = 120


class BoardClient:
    def __init__(self, api_key: str = "", base: str = BASE):
        self.api_key = (api_key or "").strip().strip("\"'")
        self.base = base.rstrip("/")

    def analyze(self, png_bytes: bytes, tasks: List[str], lens: str = "socratic", context: str = "") -> dict:
        if not self.api_key:
            raise NotSignedIn("Paste your Board AI key first.")
        data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
        body = {"image": data_url, "tasks": tasks, "lens": lens or "socratic", "context": context or ""}
        req = urllib.request.Request(
            f"{self.base}/api/public/v1/board", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": USER_AGENT, "x-api-key": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = _server_message(e.read().decode("utf-8", "replace"))
            if e.code in (401, 403):
                raise NotSignedIn(f"The Board AI key was rejected ({e.code}){msg}.") from e
            if e.code == 413:
                raise CloudError("The board image is too large for the server.") from e
            raise CloudError(f"Board AI error {e.code}{msg}") from e
        except urllib.error.URLError as e:
            raise CloudError(f"Cannot reach Board AI: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise CloudError("Board AI sent an unreadable reply.") from e


# --------------------------------------------------------------------------- lenient parsing
def _first(d: Any, *keys, default=None):
    if isinstance(d, dict):
        for k in keys:
            if d.get(k) not in (None, "", []):
                return d[k]
    return default


def _as_text_list(v) -> List[str]:
    out = []
    for x in v or []:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            t = _first(x, "question", "text", "q", "prompt", "title")
            if t:
                out.append(str(t))
        else:
            out.append(str(x))
    return out


def parse_transcription(res: dict) -> dict:
    t = res.get("transcription") if isinstance(res, dict) else None
    if isinstance(t, str):
        return {"text": t, "latex": ""}
    return {"text": str(_first(t, "text", "plain", "markdown", "content", default="")),
            "latex": str(_first(t, "latex", "math", default=""))}


def parse_explanation(res: dict) -> dict:
    e = res.get("explanation") if isinstance(res, dict) else None
    if isinstance(e, str):
        return {"title": "", "summary": e, "key_points": [], "quiz": []}
    return {"title": str(_first(e, "title", "heading", default="")),
            "summary": str(_first(e, "summary", "overview", "explanation", default="")),
            "key_points": _as_text_list(_first(e, "key_points", "keyPoints", "points", "bullets", default=[])),
            "quiz": _as_text_list(_first(e, "quiz", "questions", default=[]))}


def parse_finish(res: dict) -> dict:
    """-> {'shapes': [...], 'preview': bytes|None}. Looks in several plausible places/names."""
    f = None
    if isinstance(res, dict):
        f = _first(res, "finish", "finished", "sketch", "completion")
    shapes = _first(f, "shapes", "elements", "objects", default=None) if isinstance(f, dict) else None
    if shapes is None and isinstance(res, dict):
        shapes = _first(res, "shapes", "elements", default=[])
    preview = _first(f, "preview", "image", "previewImage", "preview_image") if isinstance(f, dict) else None
    if preview is None and isinstance(res, dict):
        preview = _first(res, "preview", "previewImage", "preview_image")
    return {"shapes": shapes if isinstance(shapes, list) else [], "preview": decode_data_url(preview)}


def decode_data_url(s) -> Optional[bytes]:
    if not isinstance(s, str) or not s:
        return None
    try:
        if s.startswith("data:"):
            s = s.split(",", 1)[1]
        return base64.b64decode(s)
    except Exception:  # noqa: BLE001
        return None


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def shapes_to_items(shapes: list, width: int, height: int) -> list:
    """Best-effort conversion of the API's editable shapes into whiteboard items.

    Accepts positions as x/y/w/h, x1/y1/x2/y2, or from/to ({x,y} or [x,y]); values <= ~1.5 are treated as fractions of
    the board, larger values as pixels of the image that was sent. Returns drawing.Item objects (unknown shapes skipped).
    """
    from .drawing import Item

    def pt(v):
        if isinstance(v, dict):
            return _num(v.get("x")), _num(v.get("y"))
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            return _num(v[0]), _num(v[1])
        return None, None

    raw = []
    for s in shapes:
        if not isinstance(s, dict):
            continue
        kind = str(_first(s, "type", "kind", "shape", default="")).lower()
        x, y = _num(s.get("x")), _num(s.get("y"))
        w, h = _num(_first(s, "w", "width")), _num(_first(s, "h", "height"))
        x1, y1, x2, y2 = (_num(s.get(k)) for k in ("x1", "y1", "x2", "y2"))
        if "from" in s and "to" in s:
            x1, y1 = pt(s["from"]); x2, y2 = pt(s["to"])
        if x1 is None and x is not None and w is not None and y is not None and h is not None:
            x1, y1, x2, y2 = x, y, x + w, y + h
        raw.append((s, kind, x, y, x1, y1, x2, y2))
    vals = [abs(v) for r in raw for v in r[2:] if v is not None]
    frac = bool(vals) and max(vals) <= 1.5
    sx, sy = (width, height) if frac else (1.0, 1.0)

    items = []
    for s, kind, x, y, x1, y1, x2, y2 in raw:
        color = str(_first(s, "color", "stroke", default="#1d4ed8"))
        if not color.startswith("#"):
            color = "#1d4ed8"
        text = str(_first(s, "text", "label", "content", default=""))
        if kind in ("text", "label") or (text and x1 is None):
            px, py = (x if x is not None else x1), (y if y is not None else y1)
            if px is not None and py is not None and text:
                items.append(Item("text", ((px * sx, py * sy),), color, 3.0, text))
        elif x1 is not None and None not in (y1, x2, y2):
            pts = ((x1 * sx, y1 * sy), (x2 * sx, y2 * sy))
            if kind in ("arrow", "connector"):
                items.append(Item("arrow", pts, color, 3.0))
            elif kind in ("line",):
                items.append(Item("line", pts, color, 3.0))
            elif kind in ("ellipse", "circle", "oval"):
                items.append(Item("ellipse", pts, color, 3.0))
            elif kind in ("box", "rect", "rectangle", "square", "frame"):
                items.append(Item("rect", pts, color, 3.0))
            if text and kind not in ("arrow", "line", "connector"):
                items.append(Item("text", ((min(x1, x2) * sx + 6, min(y1, y2) * sy + 6),), color, 2.5, text))
    return items


def notes_markdown(context: str, trans: dict, expl: dict) -> str:
    L = [f"# {expl.get('title') or context or 'Board notes'}", ""]
    if context:
        L += [f"*Topic: {context}*", ""]
    if expl.get("summary"):
        L += ["## Summary", expl["summary"], ""]
    if expl.get("key_points"):
        L += ["## Key points"] + [f"- {p}" for p in expl["key_points"]] + [""]
    if trans.get("text") or trans.get("latex"):
        L += ["## From the board"]
        if trans.get("text"):
            L += [trans["text"], ""]
        if trans.get("latex"):
            L += ["```latex", trans["latex"], "```", ""]
    if expl.get("quiz"):
        L += ["## Quiz questions"] + [f"{i}. {q}" for i, q in enumerate(expl["quiz"], 1)] + [""]
    return "\n".join(L)
