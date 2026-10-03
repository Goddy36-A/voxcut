# Changelog

## 1.4.0 - Create finished videos from a prompt
* New **Create video (AI)** tab: type a prompt, get a finished MP4 (portrait, landscape or square).
* New `voxcut/maker.py`: compose plan -> per-scene narration + picture/clip + caption -> FFmpeg render -> join -> music mix.
  Planning, voice and media are online; all rendering is local. Writes `<video>.credits.txt` with media credits.
* Fixed to the real API shapes: TTS returns `{"audioBase64","mime"}`; media returns `items[]` with a relative proxy `url`
  (resolved against the API host) and an absolute `thumb`. The API key is only sent to the API host, never to media hosts.
* New `tests/test_maker.py` (end-to-end with a fake cloud and the real compose sample `tests/sample_compose.json`).

## 1.3.0 - API-key auth
* AI Studio now uses a single **API key** field (Bearer token) instead of email + password.
* Removed the `supabase` dependency and its CI bundling flag.
* Client request shapes match the live API (`ideas` action, `trends` count, `media` orientation/page).

## 1.2.0 - AI Studio (online, optional)
* New `voxcut/cloud.py` client + "AI Studio (online)" tab: sign in, AI quote ideas, AI narration MP3.
* Also in the client (no UI yet): `compose` (reel plan), `trends`, `media` search.
* New dependency `supabase>=2.0` (sign-in); CI bundles it with `--collect-all supabase`.
* New offline test `tests/test_cloud.py`, run in CI.
* Docs: `docs/CLOUD.md`, README updated (rendering stays offline; only AI Studio needs internet).
* Version bumped 1.1.0 -> 1.2.0.
