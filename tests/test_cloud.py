"""Offline tests for voxcut.cloud using a local mock server (no network, no real account)."""
import base64, json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voxcut.cloud import CloudClient, CloudError, NotSignedIn

SEEN = []
GETS = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        GETS.append((self.path, self.headers.get("Authorization")))
        self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.end_headers()
        self.wfile.write(b"JPEGDATA")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        auth = self.headers.get("Authorization", "")
        SEEN.append((self.path, auth, body, self.headers.get("User-Agent", "")))
        if self.headers.get("User-Agent", "").startswith("Python-urllib"):
            self.send_response(403); self.end_headers(); self.wfile.write(b"<html>blocked</html>"); return
        if auth != "Bearer good-token":
            self.send_response(401); self.end_headers(); self.wfile.write(b"{}"); return
        out = {"audioBase64": base64.b64encode(b"ID3fake").decode(), "mime": "audio/mpeg"} if self.path.endswith("/tts") else {"ok": True}
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(out).encode())


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"


c = CloudClient(api_base=base)
# 1) not signed in -> refused locally, nothing is sent
try:
    c.ideas("x"); raise SystemExit("expected NotSignedIn")
except NotSignedIn:
    pass
assert not SEEN

# 2) key set -> correct paths, bodies and bearer header
c.set_key("good-token")
c.ideas("friendship", 5)
c.compose("a reel about rain")
c.trends("fitness", "tiktok")
assert c.tts("hello", "alloy") == b"ID3fake"
c.media("image", "friends walking", orientation="horizontal", page=1)
assert [s[0] for s in SEEN] == ["/api/public/v1/ideas"] * 3 + ["/api/public/v1/tts", "/api/public/v1/media"]
assert all(s[1] == "Bearer good-token" for s in SEEN)
assert SEEN[0][2] == {"action": "ideas", "topic": "friendship", "kind": "quote", "count": 5}
assert SEEN[1][2] == {"action": "compose", "prompt": "a reel about rain"}
assert SEEN[2][2] == {"action": "trends", "niche": "fitness", "platform": "tiktok", "count": 5}
assert SEEN[4][2] == {"kind": "image", "query": "friends walking", "page": 1, "orientation": "horizontal"}

# 3) server rejects a bad token -> NotSignedIn
c.set_key("bad")
try:
    c.ideas("x"); raise SystemExit("expected NotSignedIn")
except NotSignedIn:
    pass

# 3b) the User-Agent is VoxCut's (generic Python clients get blocked by some firewalls) and errors are explained
assert all(s[3].startswith("VoxCut/") for s in SEEN), [s[3] for s in SEEN]
c.set_key("bad")
try:
    c.ideas("x"); raise SystemExit("expected NotSignedIn")
except NotSignedIn as e:
    assert "401" in str(e) and "rejected the API key" in str(e), str(e)

assert CloudClient().set_key('  "Bearer qt_abc"  ') == "qt_abc"

# 4) bad media kind
try:
    c.media("gif", "x"); raise SystemExit("expected CloudError")
except CloudError:
    pass
# 5) download: relative URL (our host) carries the key; a different host never gets it
c.set_key("good-token")
d = tempfile.mkdtemp()
p = c.download("/api/public/media?u=abc", os.path.join(d, "a"))
assert p.endswith(".jpg") and open(p, "rb").read() == b"JPEGDATA", p
assert GETS[-1] == ("/api/public/media?u=abc", "Bearer good-token"), GETS
other = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=other.serve_forever, daemon=True).start()
c.download(f"http://127.0.0.1:{other.server_port}/pic", os.path.join(d, "b"))
assert GETS[-1] == ("/pic", None), GETS
print("cloud tests OK")
