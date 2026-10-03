# Cloud features (AI Studio) - contributor guide

Rendering is local (FFmpeg). Three things go online, all via the QuoteTube backend
(`https://quotetube.lovable.app`), and **all require a valid API key**:

| Feature | Endpoint | Body |
|---|---|---|
| Ideas | `POST /api/public/v1/ideas` | `{"action":"ideas","topic","kind":"quote","count"}`; `{"action":"compose","prompt"}` for a full reel plan; `{"action":"trends","niche","platform","count"}` to suggest formats |
| Narration | `POST /api/public/v1/tts` | `{"text","voice":"alloy"}` -> MP3 as base64 |
| Media search | `POST /api/public/v1/media` | `{"kind":"image"\|"video"\|"music","query","orientation":"horizontal","page":1}` |

## Auth
* The user pastes an **API key** (from the QuoteTube web app) into the AI Studio tab. It is sent on every request as
  `Authorization: Bearer <key>`. No email/password and no Supabase library are used any more.
* The key is held in memory; it is saved to QSettings (plain text in the Windows registry) **only** if the user ticks
  "Remember on this PC". Never commit a key to this repo, and never log it.
* Unauthenticated or bad-key requests are rejected server-side -> surfaced as `NotSignedIn` ("API key rejected").
* Admin settings are intentionally **not** exposed here (deferred).

## Code map
* `voxcut/cloud.py` - `CloudClient(api_key)` (no Qt): `set_key`, `ideas`, `compose`, `trends`, `tts`, `media`. Env override: `VOXCUT_API_BASE` (handy for staging).
* `voxcut/gui.py` - `page_ai()` tab "AI Studio (online)": API key, ideas, narration. `compose`, `trends` and `media`
  are implemented in the client but **not yet in the UI** - good first contributions.
* `tests/test_cloud.py` - offline tests against a local mock server.

## Known gaps / assumptions
* The TTS response field name is not documented; the client accepts `audio`, `audioContent`, `base64` or `data`.
  Confirm against the live API and tighten.
* Response shapes for ideas/media are shown as raw JSON for now.
* The live address only works once the web app is **published**.

* Status checks: an unsigned POST returns `401` JSON when the API is published; `404` HTML means the web app was not republished.
