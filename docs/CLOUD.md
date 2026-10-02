# Cloud features (AI Studio) - contributor guide

Rendering is local (FFmpeg). Three things go online, all via the QuoteTube backend
(`https://quotetube.lovable.app`), and **all require a signed-in user**:

| Feature | Endpoint | Body |
|---|---|---|
| Ideas | `POST /api/public/v1/ideas` | `{"topic","count","kind":"quote"}`; add `"action":"compose"` + `prompt` for a full reel plan, or `"action":"trends"` + `niche` + `platform` |
| Narration | `POST /api/public/v1/tts` | `{"text","voice":"alloy"}` -> MP3 as base64 |
| Media search | `POST /api/public/v1/media` | `{"kind":"image"\|"video"\|"music","query"}` |

## Auth
* Official `supabase` Python package signs in with the user's **own** web-app email + password.
* Supabase URL: `https://igzaioacedmpvqbromkk.supabase.co`; the key is the *publishable* (public) key, safe to ship.
  Never put a `service_role` / secret key in this repo.
* Each request sends `Authorization: Bearer <access_token>`; `get_session()` refreshes it automatically.
* Password is never stored; only the email is remembered (QSettings). Session is in memory only.
* Unauthenticated requests are rejected server-side (verified by the backend owner) -> surfaced as `NotSignedIn`.
* Admin settings are intentionally **not** exposed here (deferred).

## Code map
* `voxcut/cloud.py` - `CloudClient` (no Qt): `sign_in`, `ideas`, `compose`, `trends`, `tts`, `media`. Env overrides:
  `VOXCUT_SUPABASE_URL`, `VOXCUT_SUPABASE_KEY`, `VOXCUT_API_BASE` (handy for staging).
* `voxcut/gui.py` - `page_ai()` tab "AI Studio (online)": sign-in, ideas, narration. `compose`, `trends` and `media`
  are implemented in the client but **not yet in the UI** - good first contributions.
* `tests/test_cloud.py` - offline tests against a local mock server.

## Known gaps / assumptions
* The TTS response field name is not documented; the client accepts `audio`, `audioContent`, `base64` or `data`.
  Confirm against the live API and tighten.
* Response shapes for ideas/media are shown as raw JSON for now.
* The live address only works once the web app is **published**.
* PyInstaller bundling of `supabase` (`--collect-all supabase`) is untested until the first CI build.
