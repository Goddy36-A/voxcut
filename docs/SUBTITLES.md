# Subtitles / speech recognition (ASR) - contributor guide

Turn any video or audio into captions, **offline**. Replaces paid captioning tools and manual typing.

## How it works
1. `extract_audio` - ffmpeg converts the file to 16 kHz mono WAV.
2. `WhisperEngine` - runs **whisper.cpp** (`whisper-cli`, bundled in the EXE under `bin/whisper/`) with
   `-ojf -pp -t N -l <lang> [--prompt ...]`; progress comes from `-pp` lines, output from `<base>.json`.
3. `parse_whisper_json` - segments + per-word timestamps (layout verified against `examples/cli/cli.cpp`; special tokens
   such as `[_BEG_]`, `[_TT_400]`, `<|endoftext|>` are skipped; `[BLANK_AUDIO]`/`(music)` segments hidden by default).
4. `make_cues` - readable captions: <= N letters per line, <= 2 lines, max 6.5 s, breaks at sentences/pauses, min 1 s, no overlaps.
5. UI (`subtitles_ui.py`): editable table (edit text/times, merge, split, delete, find/replace, shift timing, re-split), then
   Save SRT / VTT / TXT, **burn into video** (styled ASS: position, size, colour, box, optional word-by-word highlight),
   or add a **selectable subtitle track** (mov_text, no re-encode).

## Models
Not bundled (EXE size). Downloaded once from Hugging Face (`ggerganov/whisper.cpp`) to
`%LOCALAPPDATA%/IdeawoodStudio/models` (override `IDEAWOOD_MODELS`), resumable, size-checked; or "Use a model file I already have".
Tiny/Base/Small English-only (75/142/466 MB) and Base/Small multilingual (142/466 MB). **Whisper does not support Luganda**
(Swahili and ~30 other languages are listed in `asr.LANGUAGES`). English-only models are refused for other languages.

## Verified vs not
* Verified in tests: JSON parsing (sample modelled on the real writer), cue splitting, SRT/VTT/ASS, runner (fake CLI **and the real
  `whisper-cli` binary** with the test model - correct flags, JSON path), resumable download, burn-in (captions visibly rendered),
  soft track, colon-in-path escaping (`C\\:/Windows/Fonts` - two backslashes), whole UI flow.
* CI (Windows runner, bundled FFmpeg + real `whisper-cli.exe` + real `tiny.en` model) transcribes the public-domain JFK sample and
  reports the result as a build annotation ("Real speech recognition"). That step is `continue-on-error` (it needs Hugging Face).
* **Accuracy on your audio is not verified** (accents, noisy rooms, Ugandan English). Try base.en/small.en and the vocabulary hint.

## Tips for users
Use a clean voice (the Lecture recorder can denoise first); add names/terms in "Vocabulary hint"; fix the few errors in the table;
use "Highlight each word" for reels. Long files: tiny/base are fast; small is slower but more accurate.

## Ideas
Speaker labels, translate captions, batch folders, auto-subtitle after recording, GPU builds (CUDA/Vulkan) for speed, VAD to skip silence,
word-level editing keeping karaoke timing, export for YouTube chapters from the transcript.
