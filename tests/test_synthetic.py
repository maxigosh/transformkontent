"""End-to-end check on a generated clip with known layers.

python -m pytest tests -q   (or: python tests/test_synthetic.py)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vidextract.extract import Options, run  # noqa: E402
from vidextract.localize import LocalizeOptions  # noqa: E402
from vidextract.localize import run as localize  # noqa: E402
from vidextract.media import Encoder  # noqa: E402

W, H, FPS = 360, 640, 30
WORDS = [("ONE", 0.3, 1.0), ("TWO", 1.0, 1.8), ("THREE", 2.2, 3.0)]  # (text, start, end)
CUT = 1.5                                                           # footage cut at 1.5 s
BANNERS = [(0.0, 1.6, (255, 60, 20)), (1.9, 3.2, (255, 60, 20))]     # (start, end, BGR) blue banners


def _footage(t: float) -> np.ndarray:
    """Soft, slowly drifting colour field (like the blurred fill of short-form edits)."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    if t < CUT:
        b = 40 + 10 * np.sin(xx / 70 + t)
        g = 70 + 30 * np.sin(yy / 90)
        r = 120 + 40 * np.cos((xx + yy) / 120 + t)
    else:  # a different shot
        b = 110 + 30 * np.cos(yy / 80)
        g = 60 + 10 * np.sin(xx / 60)
        r = 40 + 15 * np.sin((xx - yy) / 100)
    return np.dstack([b, g, r]).astype(np.uint8)


def _banner(frame: np.ndarray, t: float) -> None:
    for a, b, color in BANNERS:
        if a <= t < b:
            cv2.rectangle(frame, (60, 40), (300, 130), color, -1)
            cv2.putText(frame, "SHOP.COM" if a == 0 else "SALE -50%", (78, 98), cv2.FONT_HERSHEY_DUPLEX, 0.9,
                        (255, 255, 255), 2, cv2.LINE_AA)


def _caption(frame: np.ndarray, t: float) -> None:
    for text, a, b in WORDS:
        if a <= t < b:
            k = min(1.0, 0.6 + (t - a) * 8)  # quick pop-in
            scale = 1.6 * k
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 3)
            org = ((W - tw) // 2, int(H * 0.66) + th // 2)
            glyphs = np.zeros(frame.shape[:2], np.uint8)
            cv2.putText(glyphs, text, org, cv2.FONT_HERSHEY_DUPLEX, scale, 255, 3, cv2.LINE_AA)
            outline = cv2.dilate(glyphs, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
            frame[outline > 127] = 0
            frame[glyphs > 127] = 255


def make_clip(path: Path) -> None:
    enc = Encoder(path, W, H, FPS, "h264", "bgr24")
    for i in range(int(3.3 * FPS)):
        t = i / FPS
        f = _footage(t)
        _banner(f, t)
        _caption(f, t)
        enc.write(f)
    enc.close()


def test_synthetic_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "clip.mp4"
        make_clip(src)
        out = Path(tmp) / "proj"
        m = run(str(src), str(out), Options(width=W, ocr=False), log=lambda *_: None)

        ov = m["layers"]["overlay"]
        assert ov is not None, "overlay not detected"
        z = ov["zone_px"]
        assert z[1] <= 40 and z[3] >= 130, f"overlay zone {z} misses the banner"
        states = [s for s in ov["states"] if "png" in s]
        assert len(states) == 2, f"expected 2 banner states, got {[s['id'] for s in states]}"

        words = m["layers"]["captions"]["words"]
        assert len(words) == len(WORDS), f"expected {len(WORDS)} words, got {len(words)}"
        for w, (_, a, b) in zip(words, WORDS):
            assert abs(w["start"] - a) <= 2 / FPS and abs(w["end"] - b) <= 2 / FPS, (w, a, b)

        cuts = [s["start"] for s in m["shots"][1:]]
        assert any(abs(c - CUT) <= 2 / FPS for c in cuts), f"cut at {CUT}s not found: {cuts}"

        html = (out / "index.html").read_text()
        assert 'data-composition-id="main"' in html and html.count('class="clip caption-word"') == len(WORDS)
        for f in ("assets/plate.mp4", "assets/overlay.webm", "elements.json", "captions.json"):
            assert (out / f).exists(), f
        json.loads((out / "elements.json").read_text())

        # localize: logo instead of the overlay, dubbed voice, captions rebuilt from the dub
        logo = Path(tmp) / "logo.png"
        cv2.imwrite(str(logo), np.full((60, 200, 4), 255, np.uint8))
        tr = Path(tmp) / "tr.json"
        tr.write_text(json.dumps([{"id": "p0", "text": "Uno, dos, tres!"}]))  # ONE TWO THREE = one phrase
        # subtitles only: original audio and overlay stay, words follow the original speech
        audio_before = m["layers"]["audio"]
        localize(str(out), LocalizeOptions(translation=str(tr), dub=False), log=lambda *_: None)
        sub = json.loads((out / "elements.json").read_text())
        assert sub["layers"]["audio"] == audio_before and "logo" not in sub["layers"]
        sw = sub["layers"]["captions"]["words"]
        assert [w["text"] for w in sw] == ["UNO", "DOS", "TRES"]
        assert abs(sw[0]["start"] - 0.3) <= 2 / FPS and sw[-1]["end"] <= 3.3 + 1e-6
        assert 'id="overlay"' in (out / "index.html").read_text()
        localize(str(out), LocalizeOptions(translation=str(tr), dub=False, remove_overlay=True), log=lambda *_: None)
        assert 'id="overlay"' not in (out / "index.html").read_text()

        localize(str(out), LocalizeOptions(logo=str(logo), translation=str(tr), tts="mock"), log=lambda *_: None)
        loc = json.loads((out / "elements.json").read_text())
        assert loc["layers"]["audio"]["src"] == "assets/voice_en.m4a"
        assert [w["text"] for w in loc["layers"]["captions"]["words"]] == ["UNO", "DOS", "TRES"]
        assert loc["layers"]["captions"]["words"][0]["start"] >= 0.3 - 1e-6
        html = (out / "index.html").read_text()
        assert 'id="logo"' in html and 'id="overlay"' not in html and "voice_en.m4a" in html
        assert (out / "assets" / "logo.png").exists() and (out / "assets" / "voice_en.m4a").exists()


if __name__ == "__main__":
    test_synthetic_roundtrip()
    print("ok")
