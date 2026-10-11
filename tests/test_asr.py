"""Speech recognition: Deepgram (and its model fallback) turned into a caption track."""

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CALLS = []


class FakeDeepgram(BaseHTTPRequestHandler):
    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        self.rfile.read(int(self.headers["Content-Length"]))
        CALLS.append((q["model"][0], q["language"][0], self.headers.get("Authorization")))
        if q["model"][0] == "nova-3":           # pretend nova-3 does not do this language
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"err_msg":"No such model/language"}')
            return
        words = [{"word": "привет", "punctuated_word": "Привет,", "start": 0.2, "end": 0.6, "confidence": 0.99},
                 {"word": "мир", "punctuated_word": "мир!", "start": 0.7, "end": 1.1, "confidence": 0.97}]
        data = json.dumps({"results": {"channels": [{"alternatives": [{"words": words}]}]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_deepgram_transcribe():
    srv = HTTPServer(("127.0.0.1", 0), FakeDeepgram)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["DEEPGRAM_API_KEY"] = "dg-test"
    import vidextract.asr as asr
    asr.DEEPGRAM_URL = f"http://127.0.0.1:{srv.server_port}/v1/listen"
    from vidextract.media import ffmpeg_exe
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "assets").mkdir()
        subprocess.run([ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=2", str(proj / "assets/audio.m4a")],
                       check=True)
        m = {"source": {"path": "x.mp4", "width": 1080, "height": 1920, "fps": 30, "duration": 2.0},
             "canvas": {"width": 1080, "height": 1920},
             "layers": {"plate": {"src": "assets/plate.mp4"}, "overlay": None, "captions": None,
                        "audio": {"src": "assets/audio.m4a"}}}
        (proj / "elements.json").write_text(json.dumps(m))
        asr.transcribe(proj, "deepgram", "ru", log=lambda *_: None)
        caps = json.loads((proj / "elements.json").read_text())["layers"]["captions"]
        assert [w["text"] for w in caps["words"]] == ["Привет,", "мир!"]
        assert caps["words"][1]["start"] == 0.7 and caps["burned_in"] is False
        assert "caption-word" in (proj / "index.html").read_text()
    srv.shutdown()
    assert [c[0] for c in CALLS] == ["nova-3", "nova-2"] and CALLS[0][1] == "ru" and CALLS[0][2] == "Token dg-test"


if __name__ == "__main__":
    test_deepgram_transcribe()
    print("ok")
