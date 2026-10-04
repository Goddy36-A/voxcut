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

## Response shapes (verified from live samples)
* **compose** -> `{kind, templateId, fontId, perSlideSec, voice, narrate, backgroundKind, backgroundQuery, musicQuery,
  author, items:[{text, author, visual}]}` (see `tests/sample_compose.json`).
* **tts** -> `{audioBase64, mime:"audio/mpeg"}`.
* **media** -> `{items:[{id, kind, url, thumb, title, credit, creditUrl, source}], total}`. `url` is a *relative* proxy path
  (`/api/public/media?u=...`) on the API host; `thumb` is an absolute third-party URL. Not yet verified for `video`/`music` kinds.

## Create video pipeline (`voxcut/maker.py`)
1. `compose(prompt)` -> plan. 2. Per scene: `tts` (falls back to voice `alloy` if the plan voice is rejected), media search
by the scene's `visual` phrase (falls back to `backgroundQuery`; retries without `orientation`), caption PNG drawn with Qt,
FFmpeg scene clip (Ken-Burns zoom for images, loop/crop for clips). 3. Concat scenes. 4. Optional music mix (`musicQuery`).
Scene length = narration length + 0.9 s (min 3.5 s); without narration, `perSlideSec`.
Not used yet from the plan: `templateId`, `fontId` (one caption style for now) - good first contributions.

## Code map
* `voxcut/cloud.py` - `CloudClient(api_key)` (no Qt): `set_key`, `ideas`, `compose`, `trends`, `tts`, `media`. Env override: `IDEAWOOD_API_BASE` (old `VOXCUT_API_BASE` still works) (handy for staging).
* `voxcut/gui.py` - `page_ai()` tab "AI Studio (online)": API key, ideas, narration. `compose`, `trends` and `media`
  are implemented in the client but **not yet in the UI** - good first contributions.
* `voxcut/maker.py` - the video pipeline; GUI tab `page_make()` runs it in `MakerWorker`.
* `tests/test_cloud.py`, `tests/test_maker.py` - offline tests (mock server / fake cloud).

## Known gaps / assumptions
* The `ideas` and `trends` replies are shown as raw JSON in the AI Studio tab. Video/music media items are untested against the live API.
* The live address only works once the web app is **published**.

* Status checks: an unsigned POST returns `401` JSON when the API is published; `404` HTML means the web app was not republished.
