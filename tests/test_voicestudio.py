"""VoiceStudio engine against a stand-in server speaking the same OpenAI-compatible API."""

import io
import json
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vidextract.tts import VoiceStudioTTS  # noqa: E402

SEEN = {}


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        SEEN.update(path=self.path, body=json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        sr = 24000
        t = np.arange(int(1.2 * sr)) / sr
        tone = (0.3 * np.sin(2 * np.pi * 200 * t) * ((t > 0.2) & (t < 1.0))).astype(np.float32)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((tone * 32767).astype(np.int16).tobytes())
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_voicestudio_speech():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tts = VoiceStudioTTS(voice="Narrator", url=f"http://127.0.0.1:{srv.server_port}")
    audio, words = tts.speak("one longer word", speed=1.1)
    srv.shutdown()
    assert SEEN["path"] == "/v1/audio/speech"
    assert SEEN["body"]["voice"] == "Narrator" and SEEN["body"]["input"] == "one longer word"
    assert SEEN["body"]["response_format"] == "wav" and SEEN["body"]["language"] == "en"
    assert [w["text"] for w in words] == ["one", "longer", "word"]
    assert abs(words[0]["start"] - 0.2) < 0.05 and abs(words[-1]["end"] - 1.0) < 0.06   # the voiced span
    assert words[0]["end"] < words[1]["start"] + 0.03 and words[1]["end"] - words[1]["start"] > words[0]["end"] - words[0]["start"]


if __name__ == "__main__":
    test_voicestudio_speech()
    print("ok")
