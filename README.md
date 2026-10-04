# Ideawood Studio

<img src="assets/icon.png" width="96" align="right">

Video & voice studio for Windows. One `.exe`, no installs. Rendering is 100% offline and local
(FFmpeg bundled + PySide6); the optional **AI Studio** tab uses the internet (API key required).

## Features
- **AI background replacement**: cut yourself out and place yourself in front of any photo or video (e.g. your campus),
  a blurred copy of your room, or a solid colour. Runs fully offline with a bundled AI model (Robust Video Matting)
- **Voice**: noise removal, voice-clarity EQ, volume compressor, loudness boost / normalize (LUFS), extra gain
- **Format**: Landscape 16:9 / Portrait 9:16 / Square / 4:5 with blurred, solid-colour or image background (or crop-fill)
- **HD conversion**: 480p to 4K, frame-rate selection, 4 compression presets, H.264 / H.265
- **Effects & colour**: 8 looks (cinematic, vibrant, warm, cool, B&W, vintage, sepia, bright), brightness / contrast /
  saturation / gamma / warmth, sharpen, vignette, video denoise, mirror, fades, speed 0.5x-2x, trim
- **Logo / watermark** with position, size and opacity
- **Background music** that loops, fades out, and automatically ducks under your voice
- **Intro / outro** auto-joined and resized
- **Preview** a quick low-res sample (with all settings) before the full render
- **Audio / video tools**: extract audio (MP3 / WAV / M4A), save video without audio, split into both,
  add or replace audio on a video (without re-encoding the video)
- **Batch** processing, drag & drop, cancel, tray notification when finished, prevents PC sleep during renders
- **Themes**: Midnight, Graphite, Ocean, Sunset, Forest, Light + custom accent colours and text size

- **Create video (AI)**: prompt -> finished MP4 with AI plan, narration, pictures/clips, captions and music. See [docs/CLOUD.md](docs/CLOUD.md)
- **AI Studio (online, optional)**: paste your QuoteTube API key for AI quote ideas and narration (MP3). See [docs/CLOUD.md](docs/CLOUD.md)

## Get the EXE
Download `IdeawoodStudio-vX.Y.Z.exe` from **Releases**. Or push to `main` and download the **IdeawoodStudio-windows**
artifact from the Actions run. Tag a release (`git tag v1.6.0 && git push --tags`) to publish a new one.

## Run from source
```
pip install -r requirements.txt
# put ffmpeg.exe + ffprobe.exe in ./bin (or have them on PATH)
python main.py
```
Tests: `python tests/test_engine.py`, `python tests/test_matting.py`, `python tests/test_gui.py` and `python tests/test_cloud.py` and `python tests/test_maker.py` (offline, fake cloud).
Contributors: start with [CHANGELOG.md](CHANGELOG.md) and [docs/CLOUD.md](docs/CLOUD.md). Regenerate the logo: `python tools/make_icon.py`.

## Notes
- The single-file EXE is ~200 MB (FFmpeg inside) and takes a few seconds to unpack on launch.
- Windows SmartScreen may warn on unsigned apps: *More info -> Run anyway*.
- AI background replacement is the slowest feature (roughly 1-3x the video length on a typical laptop). Use **Preview** first.

## Third-party components
FFmpeg (GPL build, bundled), Robust Video Matting model by Peter Lin (GPL-3.0, `models/`), ONNX Runtime (MIT),
PySide6 (LGPL). Distributing the EXE means following the GPL terms for the FFmpeg and RVM components.

> Formerly "VoxCut". The internal Python package is still named `voxcut/` to keep imports stable; only the product name changed.
