"""Client for the QuoteTube cloud API (AI ideas, narration, media search).

Video rendering stays 100% local (FFmpeg). Only these three things go online:
  * ideas  - AI writing (quotes, full reel plans, trend ideas)
  * tts    - AI narration (MP3)
  * media  - image / video / music search

Every request needs a valid API key from the QuoteTube web app, sent as
``Authorization: Bearer <key>``. Treat the key like a password.

No Qt imports here so it can be tested and reused from scripts.
"""
import base64
import json
import os
import urllib.error
import urllib.request

# Override via env for staging.
API_BASE = os.environ.get("VOXCUT_API_BASE", "https://quotetube.lovable.app").rstrip("/")

MEDIA_KINDS = ("image", "video", "music")
IDEA_KINDS = ("quote",)  # extend as the server adds more
TIMEOUT = 60


class CloudError(Exception):
    """Any sign-in / network / API failure, with a message safe to show the user."""


class NotSignedIn(CloudError):
    pass


class CloudClient:
    """Authenticates with a QuoteTube API key sent as ``Authorization: Bearer <key>``."""

    def __init__(self, api_key="", api_base=API_BASE):
        self.api_base = api_base.rstrip("/")
        self.api_key = (api_key or "").strip()

    # ---------------- auth
    @property
    def signed_in(self):  # kept for GUI wording; True when a key is set
        return bool(self.api_key)

    def set_key(self, key):
        self.api_key = (key or "").strip()
        return self.api_key

    def clear_key(self):
        self.api_key = ""

    def access_token(self):
        if not self.api_key:
            raise NotSignedIn("Please paste your API key first.")
        return self.api_key

    # ---------------- HTTP
    def _post(self, path, payload):
        req = urllib.request.Request(
            f"{self.api_base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.access_token()}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (401, 403):
                raise NotSignedIn("The server rejected the API key (missing, wrong or revoked).") from e
            raise CloudError(f"Server error {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise CloudError(f"Cannot reach the server: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise CloudError("The server sent an unreadable response.") from e

    # ---------------- endpoints
    def ideas(self, topic, count=5, kind="quote"):
        return self._post("/api/public/v1/ideas",
                          {"action": "ideas", "topic": topic, "kind": kind, "count": int(count)})

    def compose(self, prompt):
        """Full reel plan."""
        return self._post("/api/public/v1/ideas", {"action": "compose", "prompt": prompt})

    def trends(self, niche, platform, count=5):
        """Suggest formats. platform e.g. 'reels', 'tiktok', 'shorts'."""
        return self._post("/api/public/v1/ideas",
                          {"action": "trends", "niche": niche, "platform": platform, "count": int(count)})

    def tts(self, text, voice="alloy"):
        """Returns raw MP3 bytes (server sends base64)."""
        res = self._post("/api/public/v1/tts", {"text": text, "voice": voice})
        b64 = res.get("audio") or res.get("audioContent") or res.get("base64") or res.get("data")
        if not b64:
            raise CloudError(f"No audio in response (keys: {sorted(res)}).")
        return base64.b64decode(b64)

    def media(self, kind, query, orientation=None, page=1):
        """Search media. orientation e.g. 'horizontal' (optional); page starts at 1."""
        if kind not in MEDIA_KINDS:
            raise CloudError(f"kind must be one of {MEDIA_KINDS}")
        body = {"kind": kind, "query": query, "page": int(page)}
        if orientation:
            body["orientation"] = orientation
        return self._post("/api/public/v1/media", body)
