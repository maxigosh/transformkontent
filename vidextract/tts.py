"""Text-to-speech with per-character timing (for caption sync).

ElevenLabs: POST /v1/text-to-speech/{voice_id}/with-timestamps returns the audio
plus character-level alignment. `MockTTS` produces a tone with the same shape
of output so the whole pipeline can be exercised offline.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

import numpy as np

from .media import ffmpeg_exe

SR = 44100
ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128"
DEFAULT_VOICE = "pNInz6obpgDQGcFmJgB0"   # "Adam", a stock ElevenLabs voice; pass --voice to change
DEFAULT_MODEL = "eleven_multilingual_v2"


@dataclass
class Speech:
    pcm: np.ndarray            # float32 mono at SR
    words: list[dict]          # [{"text", "start", "end"}] seconds from the clip start

    @property
    def duration(self) -> float:
        return len(self.pcm) / SR


def decode_audio(data: bytes, tempo: float = 1.0) -> np.ndarray:
    """Any ffmpeg-readable audio bytes → float32 mono PCM at SR, optionally time-stretched."""
    af = []
    t = tempo
    while t > 2.0:  # atempo takes 0.5..2.0 per stage
        af.append("atempo=2.0")
        t /= 2.0
    if abs(t - 1.0) > 1e-3:
        af.append(f"atempo={t:.4f}")
    cmd = [ffmpeg_exe(), "-v", "error", "-i", "pipe:0"]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += ["-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"]
    out = subprocess.run(cmd, input=data, capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32).copy()


def words_from_alignment(chars: list[str], starts: list[float], ends: list[float]) -> list[dict]:
    words, cur, t0, t1 = [], "", None, None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"text": cur, "start": t0, "end": t1})
            cur, t0 = "", None
            continue
        if not cur:
            t0 = s
        cur += ch
        t1 = e
    if cur:
        words.append({"text": cur, "start": t0, "end": t1})
    return words


class ElevenLabsTTS:
    def __init__(self, voice: str = DEFAULT_VOICE, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 stability: float = 0.45, similarity: float = 0.8):
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set")
        if not self.api_key.isascii() or " " in self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY does not look like a key (placeholder text?)")
        self.voice, self.model = voice, model
        self.stability, self.similarity = stability, similarity

    def speak(self, text: str, speed: float = 1.0) -> tuple[bytes, list[dict]]:
        body = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {"stability": self.stability, "similarity_boost": self.similarity,
                               "speed": round(min(1.2, max(0.7, speed)), 3)},
        }
        req = urllib.request.Request(
            ELEVEN_URL.format(voice=self.voice), data=json.dumps(body).encode(),
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"ElevenLabs {e.code}: {e.read().decode(errors='replace')[:500]}") from None
        al = data.get("alignment") or data.get("normalized_alignment")
        words = words_from_alignment(al["characters"], al["character_start_times_seconds"],
                                     al["character_end_times_seconds"])
        return base64.b64decode(data["audio_base64"]), words


class MockTTS:
    """Offline stand-in: a soft tone, ~15 chars/s, with matching word timings."""

    def speak(self, text: str, speed: float = 1.0) -> tuple[bytes, list[dict]]:
        per_char = 0.065 / speed
        words, t = [], 0.05
        for w in text.split():
            words.append({"text": w, "start": t, "end": t + per_char * len(w)})
            t += per_char * (len(w) + 1)
        n = int((t + 0.05) * SR)
        tone = (0.1 * np.sin(2 * np.pi * 220 * np.arange(n) / SR)).astype(np.float32)
        wav = subprocess.run(
            [ffmpeg_exe(), "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0", "-f", "wav", "pipe:1"],
            input=tone.tobytes(), capture_output=True, check=True).stdout
        return wav, words


def synthesize_fitted(tts, text: str, slot: float, max_tempo: float = 1.25) -> Speech:
    """Speak `text` so it fits in `slot` seconds where possible:
    first ask the voice to talk faster (up to 1.2x), then time-stretch (up to `max_tempo`)."""
    audio, words = tts.speak(text)
    pcm = decode_audio(audio)
    need = (len(pcm) / SR) / max(slot, 0.2)
    if need > 1.02:
        speed = min(1.2, need * 1.03)
        audio, words = tts.speak(text, speed=speed)
        pcm = decode_audio(audio)
        need = (len(pcm) / SR) / max(slot, 0.2)
    tempo = 1.0
    if need > 1.02:
        tempo = min(max_tempo, need * 1.02)
        pcm = decode_audio(audio, tempo)
    for w in words:
        w["start"] /= tempo
        w["end"] /= tempo
    return Speech(pcm=pcm, words=words)
