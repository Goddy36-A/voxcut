# Changelog

## 1.8.0 - Board AI for the whiteboard (includes the 1.7.1 recording fix)
* Whiteboard: new **Board AI...** button (second toolbar row). Read handwriting and formulas (text + LaTeX), explain the
  board (title, summary, key points, quiz questions), finish a sketch (editable labels/arrows/boxes + preview), and save
  lecture notes as Markdown. Uses the DoodleDraw app API with its own key (`x-api-key`).
* Finished shapes are added to a NEW whiteboard page, so the original board is never overwritten.
* New `voxcut/board_ai.py`, `voxcut/board_ai_ui.py`, `docs/BOARD_AI.md`, `tests/test_board_ai.py` (mock server).
* GitHub releases now get their notes from this changelog; release title is "Ideawood Studio vX.Y.Z".
* Heads-up: only the `read` and `explain` request/response fields are confirmed; the `finish` task name and the shape format
  are best-effort until checked against the live API (see docs/BOARD_AI.md).

## 1.7.1 - Fix: "Could not start recording" (thread_queue_size)
* The newest FFmpeg (bundled in the EXE) rejects `-thread_queue_size` on an input; the screen/device inputs no longer use it.
  Cause of the miss: tests ran on FFmpeg 6.1 while the bundle is FFmpeg master. Verified now against FFmpeg master.
* New `tests/test_windows_capture.py` (runs only on Windows, i.e. in CI with the bundled FFmpeg): real gdigrab/dshow command
  lines must be accepted by FFmpeg, and the Windows monitor/window/device/hotkey code must run.
* Recorder duration test compares against recorded wall time (FFmpeg-version independent).

## 1.7.0 - Lecture recorder for tutors
* New **Lecture recorder** tab (first tab): record a monitor, a window, a dragged area or the whiteboard - other programs
  included - with **no time limit** (segment-based, pause/resume, crash recovery), webcam picture-in-picture, microphone
  and optional computer sound.
* On-screen tools while recording: pen, marker pen, arrow, box, eraser, laser pointer, spotlight, cursor ring; floating
  toolbar that is hidden from the recording; global hotkeys (Ctrl+Alt+P/S/D/C/W/M).
* New **Whiteboard**: pages, 7 backgrounds, pen/highlighter/line/arrow/rect/ellipse/text/eraser, undo/redo, PNG + PDF export.
* Chapter markers -> `<video>.chapters.txt` for YouTube; optional voice cleanup after recording; send to editor queue.
* "Test my setup (5 seconds)" with mic level check; "Recover a recording after a crash".
* New modules `drawing.py`, `whiteboard.py`, `overlay.py`, `recorder.py`, `lecture_ui.py`; tests `test_recorder.py`,
  `test_lecture.py`; docs `docs/LECTURE.md`. Windows-only capture paths are documented as untested in CI.

## 1.6.0 - Renamed to Ideawood Studio
* Product name is now **Ideawood Studio** (window, popups, tray, README). The EXE is `IdeawoodStudio.exe`; the CI
  artifact is `IdeawoodStudio-windows`; API requests send `IdeawoodStudio/<version>` as User-Agent.
* Saved settings (including a remembered API key) are copied once from the old "VoxCut" settings location.
* Env var `IDEAWOOD_API_BASE` replaces `VOXCUT_API_BASE` (the old name is still read).
* Temp-file prefixes changed `voxcut_*` -> `ideawood_*`.
* Kept: the Python package folder `voxcut/` (internal only). Older entries below keep the old name for history.

## 1.5.1 - Fix "server rejected the API key" for valid keys
* API requests now identify as `VoxCut/<version>` instead of Python's default `Python-urllib/x`, which firewalls
  (Cloudflare) can block with a 403.
* 401 and 403 are now reported separately and include the server's own message instead of a generic "key rejected".
* The key field tolerates quotes, spaces and a pasted `Bearer ` prefix.

## 1.5.0 - AI background replacement (offline)
* New **Background** tab: replace your room with an image, a looping video, a blurred copy of your room, or a solid colour.
* Uses Robust Video Matting (ONNX, CPU) bundled in `models/` - no internet needed. Edge sharpness, brightness matching and
  720p/1080p quality options. Works together with Preview, trim, speed, effects, music, intro/outro and batch.
* New `voxcut/matting.py` and `plan_enhance()` (2-step chain: matting -> normal enhance pipeline); original audio is kept.
* New `tests/test_matting.py` verifies real pixels (corners become the new background, face/body stay) in every mode.
* Merged with 1.2.0-1.4.0 (AI Studio, Create video); the Background tab sits beside the offline tools.

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
