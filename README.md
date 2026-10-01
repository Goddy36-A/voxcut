# VoxCut

Offline desktop video enhancer for Windows. One `.exe`, no internet, no installs.
Powered by FFmpeg (bundled) + PySide6.

## Features
- **Voice**: noise removal, voice-clarity EQ, volume compressor, loudness boost/normalize (LUFS), extra gain
- **Format**: Landscape 16:9 / Portrait 9:16 / Square / 4:5, with blurred, solid-colour or image background (or crop-fill)
- **HD conversion**: 480p → 4K, frame rate selection
- **Compression**: 4 presets, H.264 or H.265
- **Intro / Outro** auto-joined and resized
- **Batch** processing, drag & drop, cancel, progress bar
- **Audio-only mode**: fix the voice without re-encoding the video (very fast)

## Get the EXE
Push to `main` → GitHub Actions builds it → download **VoxCut-windows** from the run's *Artifacts*.
Tag a release (`git tag v1.0.0 && git push --tags`) to publish `VoxCut.exe` under *Releases*.

## Run from source
```
pip install -r requirements.txt
# put ffmpeg.exe + ffprobe.exe in ./bin (or have them on PATH)
python main.py
```

## Notes
- The single-file EXE is ~150 MB (FFmpeg inside) and takes a few seconds to unpack on launch.
- Windows SmartScreen may warn on unsigned apps: *More info → Run anyway*.
