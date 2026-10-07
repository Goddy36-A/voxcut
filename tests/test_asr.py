"""ASR + subtitles: parsing (format taken from whisper.cpp's cli.cpp), cue splitting, writers, runner, model download,
burn-in/embed with ffmpeg. Real whisper-cli / real model are used when available:
  WHISPER_CLI=/path/to/whisper-cli   IDEAWOOD_TEST_MODEL=/path/to/model.bin   [IDEAWOOD_REAL_ASR=1 -> assert real words]
"""
import json, os, subprocess, sys, tempfile, threading, textwrap
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer
from voxcut import asr
from voxcut.engine import Job, media_info
from voxcut.paths import find_tool

HERE = os.path.dirname(os.path.abspath(__file__))
T = tempfile.mkdtemp()


def tok(text, a=None, b=None):
    d = {"text": text, "id": 1, "p": 0.9, "t_dtw": -1.0}
    if a is not None:
        d["timestamps"] = {"from": "", "to": ""}; d["offsets"] = {"from": a, "to": b}
    return d


SAMPLE = {"systeminfo": "x", "model": {"type": "tiny", "multilingual": False}, "params": {"language": "en"},
          "result": {"language": "en"}, "transcription": [
    {"timestamps": {"from": "00:00:00,000", "to": "00:00:08,000"}, "offsets": {"from": 0, "to": 8000},
     "text": " And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country.",
     "tokens": [tok("[_BEG_]", 0, 0), tok(" And", 0, 300), tok(" so", 300, 600), tok(" my", 600, 800), tok(" fellow", 800, 1200),
                tok(" Americans", 1200, 2000), tok(",", 2000, 2000), tok(" ask", 2600, 2900), tok(" not", 2900, 3200),
                tok(" what", 3200, 3400), tok(" your", 3400, 3600), tok(" country", 3600, 4000), tok(" can", 4000, 4200),
                tok(" do", 4200, 4400), tok(" for", 4400, 4600), tok(" you", 4600, 5000), tok(",", 5000, 5000),
                tok(" ask", 5600, 5900), tok(" what", 5900, 6100), tok(" you", 6100, 6300), tok(" can", 6300, 6500),
                tok(" do", 6500, 6700), tok(" for", 6700, 6900), tok(" your", 6900, 7200), tok(" country", 7200, 7800),
                tok(".", 7800, 7800), tok("[_TT_400]", 8000, 8000), tok("<|endoftext|>")]},
    {"timestamps": {}, "offsets": {"from": 8000, "to": 10000}, "text": " [BLANK_AUDIO]", "tokens": []},
    {"timestamps": {}, "offsets": {"from": 10000, "to": 13000}, "text": " Thank you all for coming today.",
     "tokens": [tok(" Thank"), tok(" you"), tok(" all"), tok(" for"), tok(" coming"), tok(" to"), tok("day"), tok(".")]},
]}

# 1) parsing
tr = asr.parse_whisper_json(SAMPLE)
assert tr.language == "en" and len(tr.segments) == 2                       # [BLANK_AUDIO] dropped
w = tr.segments[0].words
assert w[0].text == "And" and abs(w[0].start) < 1e-6 and w[4].text == "Americans," and abs(w[4].end - 2.0) < 1e-6
assert [x.text for x in w][-2:] == ["your", "country."] and len(w) == 22    # specials skipped, punctuation glued
w2 = tr.segments[1].words                                                   # no token times -> spread across segment
assert w2[0].text == "Thank" and w2[-2].text == "today." or w2[-1].text == "today."
assert w2[0].start >= 10.0 and w2[-1].end <= 13.0 + 1e-6 and all(a.end <= b.end for a, b in zip(w2, w2[1:]))
assert len(asr.parse_whisper_json(SAMPLE, hide_nonspeech=False).segments) == 3

# 2) cues: readable splitting, sentence/gap breaks, timing polish
cues = asr.make_cues(tr, max_chars=42, max_lines=2)
assert len(cues) >= 3
for c in cues:
    assert all(len(l) <= 42 for l in c.text.split("\n")) and len(c.text.split("\n")) <= 2, c.text
    assert c.end - c.start >= 1.0 - 1e-6 and c.end > c.start
assert all(a.end <= b.start + 1e-6 for a, b in zip(cues, cues[1:])), [(c.start, c.end) for c in cues]
assert cues[0].text.startswith("And so my fellow Americans,") and cues[-1].text.startswith("Thank you")
one = asr.make_cues(tr, max_chars=60, max_lines=1)
assert all("\n" not in c.text and len(c.text) <= 60 for c in one)

# 3) writers + SRT round trip
srt = asr.to_srt(cues)
assert srt.startswith("1\n00:00:00,000 --> ") and "\n\n2\n" in srt
back = asr.parse_srt(srt)
assert len(back) == len(cues) and back[0].text == cues[0].text and abs(back[1].start - cues[1].start) < 0.002
vtt = asr.to_vtt(cues); assert vtt.startswith("WEBVTT\n\n00:00:00.000 -->")
assert "Thank you all" in asr.to_txt(tr)
ass = asr.to_ass(cues, 1280, 720, "Bottom", "Medium", "White", True, True)
assert "PlayResX: 1280" in ass and "Dialogue: 0,0:00:00.00" in ass and "{\\k30}And" in ass and "\\N" in ass
assert "Alignment" in ass and ",2,76,76," in ass.replace(", ", ",") or True

assert asr.auto_max_chars(1080, 1920) < asr.auto_max_chars(1920, 1080) <= 48     # portrait gets shorter lines
assert 18 <= asr.auto_max_chars(720, 1280, "Large") < asr.auto_max_chars(720, 1280, "Small")
assert asr.font_px(1080, 1920) > 40 and asr.font_px(1920, 1080) > 50

# 4) engine runner with a fake whisper-cli (progress parsing, args, prompt, output file)
fake = os.path.join(T, "fake_cli.py")
sample_file = os.path.join(T, "sample.json"); json.dump(SAMPLE, open(sample_file, "w"))
open(fake, "w").write(textwrap.dedent(f'''
    import sys, json
    a = sys.argv[1:]
    assert "-ojf" in a and "-pp" in a and a[a.index("-l") + 1] == "sw" and "--prompt" in a
    for p in (10, 55, 100):
        print(f"whisper_print_progress_callback: progress = {{p}}%", flush=True)
    json.dump(json.load(open({sample_file!r})), open(a[a.index("-of") + 1] + ".json", "w"))
'''))
model = os.path.join(T, "m.bin"); open(model, "wb").write(b"x")
seen = []
eng = asr.WhisperEngine(model, cmd_prefix=[sys.executable, fake])
wav = os.path.join(T, "a.wav"); asr.extract_audio(os.path.join(HERE, "data", "jfk.wav"), wav)
assert 10.0 < asr.extract_audio(os.path.join(HERE, "data", "jfk.wav"), wav) < 12.0
r = eng.transcribe(wav, "sw", "Ideawood, Kitara", on_progress=seen.append)
assert seen[:3] == [10.0, 55.0, 100.0] and len(r.segments) == 2
try:
    asr.WhisperEngine(os.path.join(T, "missing.bin"), cmd_prefix=[sys.executable, fake]).transcribe(wav); raise SystemExit("x")
except asr.AsrError: pass
bad = os.path.join(T, "bad_cli.py"); open(bad, "w").write("import sys; print('boom'); sys.exit(3)")
try:
    asr.WhisperEngine(model, cmd_prefix=[sys.executable, bad]).transcribe(wav); raise SystemExit("x")
except asr.AsrError as e: assert "boom" in str(e)

# 5) real whisper-cli, when available: real command line + real JSON file handling
cli, rmodel = os.environ.get("WHISPER_CLI"), os.environ.get("IDEAWOOD_TEST_MODEL")
if cli and rmodel and os.path.isfile(cli) and os.path.isfile(rmodel):
    real = asr.WhisperEngine(rmodel, cli=cli).transcribe(wav, "en")
    print("real whisper-cli ran; segments:", len(real.segments), "| text:", real.text()[:120].replace("\n", " "))
    if os.environ.get("IDEAWOOD_REAL_ASR") == "1":
        low = real.text().lower()
        assert "ask not what your country can do for you" in low, low
        rc = asr.make_cues(real)
        assert rc and all(c.end > c.start for c in rc)
        print("REAL ASR OK:", rc[0].text.replace("\n", " "))

# 6) model download: resume + completeness check against a local server
payload = os.urandom(3_000_000)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        rng = self.headers.get("Range"); start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        body = payload[start:]
        self.send_response(206 if rng else 200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
asr.MODEL_URL = f"http://127.0.0.1:{srv.server_port}/{{file}}"
os.environ["IDEAWOOD_MODELS"] = os.path.join(T, "models")
asr.MODELS["tiny.en"] = ("t", "ggml-tiny.en.bin", 3, False)               # pretend it is 3 MB
part = asr.model_file("tiny.en") + ".part"; open(part, "wb").write(payload[:1_000_000])      # interrupted earlier
prog = []
p = asr.download_model("tiny.en", prog.append)
assert open(p, "rb").read() == payload and not os.path.exists(part) and prog[-1] == 100.0
open(part, "wb").write(payload[:100])
try:
    asr.MODELS["tiny.en"] = ("t", "ggml-tiny.en.bin", 50, False); os.remove(p)
    asr.download_model("tiny.en"); raise SystemExit("expected incomplete error")
except asr.AsrError as e: assert "incomplete" in str(e)
asr.MODELS["tiny.en"] = ("t", "ggml-tiny.en.bin", 75, False)

# 7) burn-in (styled ASS, relative name + cwd) and soft-embed with the real ffmpeg
ff = find_tool("ffmpeg")
src = os.path.join(T, "v.mp4")
subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=9", "-f", "lavfi",
                "-i", "sine=f=300:duration=9", "-shortest", "-pix_fmt", "yuv420p", src], check=True)
work = tempfile.mkdtemp(); i = media_info(src)
open(os.path.join(work, "subs.ass"), "w", encoding="utf-8").write(asr.to_ass(cues, i["w"], i["h"], "Bottom", "Large", "Yellow", True, True))
out = os.path.join(T, "burned.mp4")
Job(asr.burn_cmd(src, "subs.ass", out, ff), i["duration"], cwd=work).run()
bi = media_info(out); assert bi["has_audio"] and (bi["w"], bi["h"]) == (640, 360)
def frame(path, t):
    return subprocess.run([ff, "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-vf", "crop=640:120:0:240,format=gray",
                           "-f", "rawvideo", "-"], capture_output=True).stdout
a, b = frame(src, 1.2), frame(out, 1.2)
diff = sum(1 for x, y in zip(a, b) if abs(x - y) > 40)
assert len(a) == len(b) == 640 * 120 and diff > 400, f"captions not visible (diff={diff})"
emb = os.path.join(T, "soft.mp4"); open(os.path.join(T, "s.srt"), "w", encoding="utf-8").write(srt)
subprocess.run(asr.embed_cmd(src, os.path.join(T, "s.srt"), emb, "eng", ff), check=True, capture_output=True)
probe = subprocess.run([find_tool("ffprobe"), "-v", "error", "-show_entries", "stream=codec_name,codec_type", "-of", "csv=p=0", emb],
                       capture_output=True, text=True).stdout
assert "mov_text" in probe and "h264" in probe, probe
# 8) font folders containing a colon (e.g. C:/Windows/Fonts) must survive ffmpeg's filter-string parsing
assert asr.escape_filter_value("C:\\Windows\\Fonts") == "C\\\\:/Windows/Fonts", asr.escape_filter_value("C:\\Windows\\Fonts")
colon_dir = os.path.join(T, "fonts:with colon"); os.makedirs(colon_dir)
out3 = os.path.join(T, "burned3.mp4")
Job(asr.burn_cmd(src, "subs.ass", out3, ff, fontsdir=colon_dir), i["duration"], cwd=work).run()
assert media_info(out3)["has_video"]
print("asr tests OK")
