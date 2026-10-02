# Changelog

## 1.2.0 - AI Studio (online, optional)
* New `voxcut/cloud.py` client + "AI Studio (online)" tab: sign in, AI quote ideas, AI narration MP3.
* Also in the client (no UI yet): `compose` (reel plan), `trends`, `media` search.
* New dependency `supabase>=2.0` (sign-in); CI bundles it with `--collect-all supabase`.
* New offline test `tests/test_cloud.py`, run in CI.
* Docs: `docs/CLOUD.md`, README updated (rendering stays offline; only AI Studio needs internet).
* Version bumped 1.1.0 -> 1.2.0.
