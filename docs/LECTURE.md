# Lecture recorder - contributor guide

For tutors/lecturers: record any screen, window or area (other programs included) with **no time limit**, draw on
screen, use a whiteboard, drop chapter markers, then clean the voice and send the result to the editor queue.
Replaces recorders with 5-minute limits.

## Features
| Area | What it does | Code |
|---|---|---|
| Sources | whole monitor, one window (its area at start), drag-selected area, the whiteboard window | `recorder.py` (`list_monitors`, `list_windows`, `window_rect`), `overlay.RegionSelector` |
| Audio / video | microphone, optional computer sound (e.g. "Stereo Mix"), webcam picture-in-picture (4 corners, 3 sizes) | `recorder.build_segment_cmd` |
| No time limit | MKV segments in `<name>.parts/`; Pause = close segment, Resume = new one; Stop joins with stream copy | `recorder.Recorder`, `finalize_parts` |
| Crash safety | leftover `.parts` folder can be rebuilt ("Recover a recording..." button) | `recorder.recover_parts` |
| Chapters | **Marker** button / Ctrl+Alt+M -> `<video>.chapters.txt` ready to paste into a YouTube description | `recorder.write_chapters` |
| On-screen tools | pen, marker pen, arrow, box, eraser, laser pointer, spotlight, cursor ring, undo, clear | `overlay.ScreenOverlay` |
| Floating toolbar | pause/stop/marker/tools/colours/whiteboard; hidden from the recording (`WDA_EXCLUDEFROMCAPTURE`) | `overlay.RecorderBar`, `recorder.exclude_from_capture` |
| Hotkeys (global) | Ctrl+Alt+ P pause, S stop, D draw on/off, C clear, W whiteboard, M marker | `overlay.Hotkeys` (Win32 `RegisterHotKey`) |
| Whiteboard | pages, white/cream/chalkboard/black/grid/lines/graph backgrounds, pen, highlighter, line, arrow, rect, ellipse, text, stroke eraser, undo/redo, PNG + multi-page PDF export | `whiteboard.py`, `drawing.py` |
| After recording | optional voice cleanup (denoise + compressor + loudness), then "Send to editor queue" | `recorder.clean_voice_cmd`, `lecture_ui.FinishWorker` |
| Setup check | "Test my setup (5 seconds)" records, measures mic level and plays it back | `recorder.analyze_audio` |
| Mic meter | live level bar while choosing the microphone | `lecture_ui.MicMeter` (Qt Multimedia) |

## Architecture
* `drawing.py` - Qt-free vector model (`Item`, `DrawModel`: undo/redo snapshots, stroke erase, hit tests) + `paint_item`.
* `recorder.py` - everything FFmpeg/Windows: device + monitor + window discovery (ctypes), command builder, `Recorder`.
  `RecordConfig.video_input/mic_input/cam_input/sys_input` replace the real capture inputs, so tests run anywhere.
* `overlay.py`, `whiteboard.py` - Qt widgets. `lecture_ui.py` - the tab (`LecturePanel`) + session controller.
* Recording never passes `-t`. Default encode is x264 `ultrafast` crf 26 ("light") so a 2-hour lecture stays cheap.
* Screens larger than 1920 px wide are scaled down while recording (`RecordConfig.max_width`).

## Tests
* `tests/test_recorder.py` - dshow parsing, command shape, record/mark/pause/resume/stop, webcam PiP, bad input, crash recovery, voice cleanup.
* `tests/test_lecture.py` - drawing model, whiteboard (all tools, pages, PNG/PDF), overlay (draw/erase/laser/spotlight, click-through flags),
  recorder bar, and the whole GUI flow incl. "closing the app mid-lecture saves the video".
* Both use FFmpeg test patterns + offscreen Qt, run in CI.

## NOT verifiable in CI - check on a real Windows PC
* real `gdigrab` capture of other apps, multi-monitor offsets, high-DPI scaling (we use physical pixels from `EnumDisplayMonitors`)
* `dshow` device names and webcam resolution defaults; "Stereo Mix" availability
* the toolbar being invisible in the recording (needs Windows 10 2004+; otherwise it will be visible)
* global hotkeys (`RegisterHotKey`) and click-ring (`GetAsyncKeyState`)
* GPU-accelerated windows via region capture are fine; DRM-protected video (Netflix etc.) records black by design.
Use the in-app **Test my setup** button and report what fails.

## Known limitations / ideas
* "One window" records the window's area at the moment recording starts; if the window is moved, the recording does not follow.
* Computer sound needs a capture device (Stereo Mix / virtual cable). Native WASAPI loopback is not in FFmpeg.
* Overlay has no text tool and no image paste; the whiteboard has no image paste or moving of items.
* Ideas: slide timer, teleprompter, per-segment re-record, hardware encoders (NVENC/QSV), webcam preview before recording.
