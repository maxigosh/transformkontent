"""Localize an extracted project: swap the overlay for a brand logo, drop the
original speech, translate it, dub it (ElevenLabs) and rebuild the captions
from the new voice's timing.

    python -m vidextract localize my_project --logo logo.png --voice <id>
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import compose
from . import logo as logo_mod
from .media import ffmpeg_exe
from .translate import load_translation, phrases_from_words, translate_claude
from .tts import SR, ElevenLabsTTS, MockTTS, synthesize_fitted


@dataclass
class LocalizeOptions:
    logo: str | None = None
    lang: str = "English"
    lang_code: str = "en"
    translation: str | None = None       # use this translation file instead of calling Claude
    translate_only: bool = False         # stop after writing translation.<code>.json
    tts: str = "elevenlabs"              # elevenlabs | mock
    voice: str | None = None
    tts_model: str | None = None
    min_gap: float = 0.08                # seconds of air between dubbed phrases
    dub: bool = True                     # False: keep the original audio, only translate the captions
    remove_overlay: bool = False         # drop the extracted overlay (the clean plate has it painted out)
    keep_ambience: bool = True           # with a dub: keep the original track between phrases (laughter, room)


def _log(msg: str) -> None:
    print(f"[vidextract] {msg}", flush=True)


def _write_m4a(pcm: np.ndarray, out: Path) -> None:
    channels = 1 if pcm.ndim == 1 else pcm.shape[1]
    subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", str(channels), "-i", "pipe:0",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(SR), "-c:a", "aac", "-b:a", "160k", str(out)],
        input=np.ascontiguousarray(np.clip(pcm, -1, 1), np.float32).tobytes(), check=True)


def run(project: str, opt: LocalizeOptions, log=_log) -> dict:
    proj = Path(project)
    m = json.loads((proj / "elements.json").read_text())
    layers = m["layers"]
    D = m["source"]["duration"]
    W, H = m["canvas"]["width"], m["canvas"]["height"]

    # ---- 1. logo instead of the extracted overlay ---------------------------------
    if opt.logo:
        src = Path(opt.logo)
        prepared = logo_mod.prepare(src, proj / "assets")
        ov = layers.get("overlay")
        zone = ov["zone_px"] if ov else [0, int(H * 0.06), W, int(H * 0.26)]
        beats = [0.0]
        if ov:
            beats += [seg["start"] for seg in ov["timeline"]
                      if seg["kind"] == "state" and seg["start"] > 0.5]
        layers["logo"] = {**prepared, "zone_px": zone, "beats": sorted(set(beats))}
        parts = "mark + wordmark animated separately" if prepared["mark"] else "single piece"
        log(f"logo: {src.name} in zone y={zone[1]}..{zone[3]}, {parts}, {len(beats)} pop beats")

    ov = layers.get("overlay")
    if ov:
        ov["hidden"] = opt.remove_overlay
        if opt.remove_overlay:
            log("overlay: removed (footage under it is the inpainted clean plate)")

    # ---- 2. speech → phrases → translation ------------------------------------------
    audio = layers.get("audio")
    if audio and "source_audio" not in layers and "voice-over" not in audio.get("label", ""):
        layers["source_audio"] = audio["src"]

    caps = layers.get("captions")
    if not caps:
        raise RuntimeError("no captions in this project — nothing to translate")
    src_words = caps.get("source_words") or caps["words"]
    cuts = [s["start"] for s in m.get("shots", [])[1:]]
    phrases = phrases_from_words(src_words, cuts)
    tr_path = proj / f"translation.{opt.lang_code}.json"
    if opt.translation:
        tr = load_translation(opt.translation)
        log(f"translation: {len(tr)} phrases from {opt.translation}")
    elif tr_path.exists():
        tr = load_translation(tr_path)
        log(f"translation: reusing {tr_path.name} (delete it to re-translate)")
    else:
        log(f"translation: {len(phrases)} phrases → {opt.lang} via Claude…")
        tr = translate_claude(phrases, opt.lang)
    for p in phrases:
        p["text"] = tr.get(p["id"], "")
    tr_path.write_text(json.dumps(phrases, ensure_ascii=False, indent=1))
    if opt.translate_only:
        log(f"wrote {tr_path} — review/edit it, then run again without --translate-only")
        return m

    # ---- 3. dub, or keep the original speech and only retime words to it ----------
    voice_rel = None
    if opt.dub:
        bed = None
        if opt.keep_ambience and layers.get("source_audio"):
            bed = _ambience_bed(proj / layers["source_audio"], src_words, D)
            log("ambience: original track kept between phrases (laughter, room), muted under speech")
        words, voice_rel = _dub(proj, phrases, D, opt, log, bed)
    else:
        words = _words_on_original_timing(phrases, D)
        log(f"subtitles only: {len(words)} words placed on the original speech timing")

    # ---- 4. one-word captions from the new timing---------------------------------------
    new_words = []
    for i, w in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < len(words) else w["end"] + 0.3
        end = nxt if nxt - w["end"] < 0.3 else w["end"] + 0.12  # hold until the next word, like the source
        new_words.append({"id": f"w{i}", "text": w["text"].strip(".,!?;:\"«»“”").upper() or w["text"],
                          "start": w["start"], "end": round(min(end, D), 3)})
    caps.setdefault("source_words", caps["words"])
    caps["words"] = new_words
    caps["lang"] = opt.lang_code

    # ---- 5. audio: with a dub the original soundtrack is dropped, only the dub plays ---
    if voice_rel:
        layers["audio"] = {"src": voice_rel, "label": f"{opt.lang} voice-over ({opt.tts})"}
    m["lang"] = opt.lang_code
    (proj / "elements.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
    (proj / f"captions.{opt.lang_code}.json").write_text(json.dumps(new_words, ensure_ascii=False, indent=1))
    compose.write_project(proj, m)
    log(f"done: {len(phrases)} phrases, {len(new_words)} caption words → {proj / 'index.html'}")
    return m


def _dub(proj: Path, phrases: list[dict], D: float, opt: LocalizeOptions, log,
         bed: np.ndarray | None = None) -> tuple[list[dict], str]:
    """Voice each phrase in its time slot; returns word timings and the voice-over path."""
    if opt.tts == "mock":
        tts = MockTTS()
    else:
        kw = {}
        if opt.voice:
            kw["voice"] = opt.voice
        if opt.tts_model:
            kw["model"] = opt.tts_model
        tts = ElevenLabsTTS(**kw)
    track = np.zeros(int((D + 1.0) * SR), np.float32)
    words: list[dict] = []
    cursor = 0.0
    overflow = 0
    for p in phrases:
        text = p["text"].strip()
        if not text:
            continue
        start = max(p["start"], cursor)
        slot_end = min(D, p["slot_end"] if p["slot_end"] is not None else D)
        speech = synthesize_fitted(tts, text, slot_end - start)
        a = int(start * SR)
        b = min(len(track), a + len(speech.pcm))
        track[a:b] += speech.pcm[:b - a]
        end = start + speech.duration
        if end > slot_end + 0.05:
            overflow += 1
        cursor = end + opt.min_gap
        for w in speech.words:
            words.append({"text": w["text"], "start": round(start + w["start"], 3),
                          "end": round(start + w["end"], 3)})
        log(f"  {p['id']} {start:6.2f}s  {speech.duration:4.1f}s/{slot_end - start:4.1f}s  {text}")
    track = track[: int(D * SR)]
    if bed is not None:
        n = min(len(track), len(bed))
        track = bed[:n] + track[:n, None]
    voice_rel = f"assets/voice_{opt.lang_code}.m4a"
    _write_m4a(track, proj / voice_rel)
    if overflow:
        log(f"  {overflow} phrase(s) ran past their slot and pushed the next one later")
    return words, voice_rel


def _words_on_original_timing(phrases: list[dict], D: float, min_word: float = 0.22) -> list[dict]:
    """Spread each translated phrase over the time its original was spoken.

    Words get time in proportion to their length; a phrase whose translation needs
    more room than the original took may run on into the pause before the next one.
    """
    words: list[dict] = []
    for p in phrases:
        ew = p["text"].split()
        if not ew:
            continue
        start = p["start"]
        slot_end = min(D, p["slot_end"] if p["slot_end"] is not None else D)
        end = max(p["end"], min(slot_end - 0.05, start + min_word * len(ew)))
        end = max(end, start + 0.2)
        weights = np.array([len(w) + 2 for w in ew], np.float64)
        bounds = start + (end - start) * np.r_[0, np.cumsum(weights)] / weights.sum()
        for w, a, b in zip(ew, bounds[:-1], bounds[1:]):
            words.append({"text": w, "start": round(float(a), 3), "end": round(float(b), 3)})
    return words


def _ambience_bed(path: Path, src_words: list[dict], D: float, pre: float = 0.12, post: float = 0.18,
                  ramp: float = 0.08) -> np.ndarray:
    """The original track with the speaker muted: the caption timings say when he talks,
    so those spans are faded out and everything between them (audience laughter, room
    tone, applause) is kept to sit under the dub."""
    raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2",
                          "-ar", str(SR), "pipe:1"], capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, np.float32).reshape(-1, 2).copy()
    n = len(audio)
    spans: list[list[float]] = []
    for w in src_words:
        a, b = w["start"] - pre, w["end"] + post
        if spans and a - spans[-1][1] < 0.25:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    gain = np.ones(n, np.float32)
    r = max(1, int(ramp * SR))
    fade = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, r, dtype=np.float32))   # 0 → 1
    for a, b in spans:
        i, j = max(0, int(a * SR)), min(n, int(b * SR))
        if j <= i:
            continue
        gain[i:j] = 0
        lo = max(0, i - r)
        gain[lo:i] = np.minimum(gain[lo:i], fade[::-1][r - (i - lo):])
        hi = min(n, j + r)
        gain[j:hi] = np.minimum(gain[j:hi], fade[: hi - j])
    return audio * gain[:, None]
