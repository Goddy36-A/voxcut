"""Client for the QuoteTube cloud API (AI ideas, narration, media search).

Video rendering stays 100% local (FFmpeg). Only these three things go online:
  * ideas  - AI writing (quotes, full reel plans, trend ideas)
  * tts    - AI narration (MP3)
  * media  - image / video / music search

Every request needs a signed-in user. Sign in with the same email + password as the
QuoteTube web app; the access token is sent as ``Authorization: Bearer <token>``.
The password is never stored - the session lives in memory only.

No Qt imports here so it can be tested and reused from scripts.
"""
import base64
import json
import os
import urllib.error
import urllib.request

# Public (publishable) values - safe to ship in the client. Override via env for staging.
SUPABASE_URL = os.environ.get("VOXCUT_SUPABASE_URL", "https://igzaioacedmpvqbromkk.supabase.co")
SUPABASE_PUBLIC_KEY = os.environ.get("VOXCUT_SUPABASE_KEY", "sb_publishable_0ZYwjRAmpgTAbP7ZQfVO3g_0Wqs3r46")
API_BASE = os.environ.get("VOXCUT_API_BASE", "https://quotetube.lovable.app").rstrip("/")

MEDIA_KINDS = ("image", "video", "music")
IDEA_KINDS = ("quote",)  # extend as the server adds more
TIMEOUT = 60


class CloudError(Exception):
    """Any sign-in / network / API failure, with a message safe to show the user."""


class NotSignedIn(CloudError):
    pass


class CloudClient:
    def __init__(self, supabase_url=SUPABASE_URL, public_key=SUPABASE_PUBLIC_KEY, api_base=API_BASE):
        self.supabase_url, self.public_key, self.api_base = supabase_url, public_key, api_base.rstrip("/")
        self._sb = None
        self.email = None

    # ---------------- auth
    def _client(self):
        if self._sb is None:
            try:
                from supabase import create_client  # official sign-in library
            except ImportError as e:  # pragma: no cover
                raise CloudError("The 'supabase' package is not installed (pip install supabase).") from e
            self._sb = create_client(self.supabase_url, self.public_key)
        return self._sb

    @property
    def signed_in(self):
        return self.email is not None

    def sign_in(self, email, password):
        try:
            res = self._client().auth.sign_in_with_password({"email": email.strip(), "password": password})
        except CloudError:
            raise
        except Exception as e:  # noqa: BLE001
            raise CloudError(f"Sign-in failed: {e}") from e
        if not getattr(res, "session", None):
            raise CloudError("Sign-in failed: no session returned.")
        self.email = email.strip()
        return self.email

    def sign_out(self):
        try:
            if self._sb is not None:
                self._sb.auth.sign_out()
        except Exception:  # noqa: BLE001
            pass
        self._sb, self.email = None, None

    def access_token(self):
        """Current access token; the library refreshes it automatically when it expires."""
        if not self.signed_in:
            raise NotSignedIn("Please sign in first.")
        try:
            session = self._client().auth.get_session()
        except Exception as e:  # noqa: BLE001
            raise NotSignedIn(f"Session expired, please sign in again ({e}).") from e
        if not session or not session.access_token:
            raise NotSignedIn("Session expired, please sign in again.")
        return session.access_token

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
                raise NotSignedIn("Not authorised - please sign in again.") from e
            raise CloudError(f"Server error {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise CloudError(f"Cannot reach the server: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise CloudError("The server sent an unreadable response.") from e

    # ---------------- endpoints
    def ideas(self, topic, count=5, kind="quote"):
        return self._post("/api/public/v1/ideas", {"topic": topic, "count": int(count), "kind": kind})

    def compose(self, prompt):
        """Full reel plan."""
        return self._post("/api/public/v1/ideas", {"action": "compose", "prompt": prompt})

    def trends(self, niche, platform):
        return self._post("/api/public/v1/ideas", {"action": "trends", "niche": niche, "platform": platform})

    def tts(self, text, voice="alloy"):
        """Returns raw MP3 bytes (server sends base64)."""
        res = self._post("/api/public/v1/tts", {"text": text, "voice": voice})
        b64 = res.get("audio") or res.get("audioContent") or res.get("base64") or res.get("data")
        if not b64:
            raise CloudError(f"No audio in response (keys: {sorted(res)}).")
        return base64.b64decode(b64)

    def media(self, kind, query):
        if kind not in MEDIA_KINDS:
            raise CloudError(f"kind must be one of {MEDIA_KINDS}")
        return self._post("/api/public/v1/media", {"kind": kind, "query": query})
