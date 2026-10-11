"""Speech recognition for videos without captions burned in.

The words and their timings become the project's caption track, so the rest
of the pipeline (phrases → translation → dub → new captions) runs as it does
for a clip whose captions were read off the frames.

    python -m vidextract transcribe my_project --engine deepgram --lang ru

Engines:
  deepgram  Deepgram's /v1/listen (nova-3, nova-2 as a fallback); DEEPGRAM_API_KEY
  whisper   Whisper inside a local VoiceStudio (OpenAI-compatible
            /v1/audio/transcriptions); VOICESTUDIO_URL, VOICESTUDIO_API_KEY
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import compose
from .media import ffmpeg_exe

DEEPGRAM_URL = os.environ.get("DEEPGRAM_URL", "https://api.deepgram.com/v1/listen")


def _wav(src: Path) -> bytes:
    """16 kHz mono WAV: small, and what every recogniser takes."""
    return subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                           "-f", "wav", "pipe:1"], capture_output=True, check=True).stdout


def deepgram(audio: bytes, lang: str, api_key: str | None = None) -> list[dict]:
    key = api_key or os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY is not set")
    last = None
    for model in ("nova-3", "nova-2"):
        q = urllib.parse.urlencode({"model": model, "language": lang, "smart_format": "true", "punctuate": "true"})
        req = urllib.request.Request(f"{DEEPGRAM_URL}?{q}", data=audio,
                                     headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.loads(r.read())
            alt = data["results"]["channels"][0]["alternatives"][0]
            return [{"text": w.get("punctuated_word") or w["word"], "start": float(w["start"]), "end": float(w["end"]),
                     "confidence": float(w.get("confidence", 1.0))} for w in alt.get("words", [])]
        except urllib.error.HTTPError as e:
            last = f"Deepgram {e.code} ({model}): {e.read().decode(errors='replace')[:300]}"
            if e.code != 400:      # a 400 is usually "this model doesn't do this language": try the next
                break
    raise RuntimeError(last or "Deepgram failed")


def whisper_voicestudio(audio: bytes, lang: str) -> list[dict]:
    url = (os.environ.get("VOICESTUDIO_URL") or "http://127.0.0.1:3900").rstrip("/") + "/v1/audio/transcriptions"
    key = os.environ.get("VOICESTUDIO_API_KEY", "").strip()
    b = uuid.uuid4().hex
    fields = {"model": "whisper-1", "language": lang, "response_format": "verbose_json",
              "timestamp_granularities[]": "word"}
    body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                    for k, v in fields.items())
    body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
             f"Content-Type: audio/wav\r\n\r\n").encode() + audio + f"\r\n--{b}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={b}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=1800) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"VoiceStudio transcription {e.code}: {e.read().decode(errors='replace')[:300]}") from None
    words = data.get("words") or [w for s in data.get("segments", []) for w in s.get("words", [])]
    return [{"text": (w.get("word") or w.get("text") or "").strip(), "start": float(w["start"]), "end": float(w["end"]),
             "confidence": 1.0} for w in words if (w.get("word") or w.get("text") or "").strip()]


def default_style(W: int, H: int) -> dict:
    """Captions for a clip that had none: one bold white outlined word at a time, a little
    below the middle, with a springy pop (the same look as the outlined-word style)."""
    return {"center": {"x": 0.5, "y": 0.68}, "cap_height_frac": 0.03, "cap_height_px": round(0.03 * H, 1),
            "text_color": "#ffffff", "stroke_color": "#000000", "stroke_px": round(8.5 * W / 1080, 1),
            "uppercase": True, "words_per_caption": 1,
            "pop_in": {"fps": 30.0, "scales": [0.7, 1.3, 1.25, 1.08, 0.94, 0.99]}, "exit": {"scales": [], "hard_cut": True}}


def transcribe(project: str | Path, engine: str = "deepgram", lang: str = "ru", log=print) -> dict:
    proj = Path(project)
    m = json.loads((proj / "elements.json").read_text())
    audio_rel = (m["layers"].get("audio") or {}).get("src")
    if not audio_rel:
        raise SystemExit("this project has no audio track to recognise")
    audio = _wav(proj / audio_rel)
    log(f"speech recognition: {engine} ({lang}), {len(audio) / 32000:.0f} s of audio…")
    words = deepgram(audio, lang) if engine == "deepgram" else whisper_voicestudio(audio, lang)
    if not words:
        raise SystemExit("no speech recognised")
    W, H = m["canvas"]["width"], m["canvas"]["height"]
    m["layers"]["captions"] = {
        "style": default_style(W, H),
        "source": f"speech recognition ({engine})",
        "burned_in": False,     # nothing to cover in the frame: new captions are simply added
        "words": [{"id": f"w{i}", "text": re.sub(r"\s+", " ", w["text"]), "start": round(w["start"], 3),
                   "end": round(max(w["end"], w["start"] + 0.08), 3), "confidence": round(100 * w["confidence"], 1)}
                  for i, w in enumerate(words)],
    }
    (proj / "elements.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
    (proj / "captions.json").write_text(json.dumps(
        [{k: w[k] for k in ("id", "text", "start", "end")} for w in m["layers"]["captions"]["words"]],
        ensure_ascii=False, indent=1))
    compose.write_project(proj, m)
    log(f"speech recognition: {len(words)} words → captions track")
    return m
