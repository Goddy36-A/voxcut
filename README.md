# VoxCut

<img src="assets/icon.png" width="96" align="right">

Offline video & voice studio for Windows. One `.exe`, no internet, no installs.
Powered by FFmpeg (bundled) + PySide6.

## Features
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

## Get the EXE
Download `VoxCut-vX.Y.Z.exe` from **Releases**. Or push to `main` and download the **VoxCut-windows**
artifact from the Actions run. Tag a release (`git tag v1.2.0 && git push --tags`) to publish a new one.

## Run from source
```
pip install -r requirements.txt
# put ffmpeg.exe + ffprobe.exe in ./bin (or have them on PATH)
python main.py
```
Tests: `python tests/test_engine.py` and `python tests/test_gui.py`. Regenerate the logo: `python tools/make_icon.py`.

## Notes
- The single-file EXE is ~200 MB (FFmpeg inside) and takes a few seconds to unpack on launch.
- Windows SmartScreen may warn on unsigned apps: *More info -> Run anyway*.
