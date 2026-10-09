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
from .tts import SR, CachedTTS, ElevenLabsTTS, MockTTS, synthesize_fitted


@dataclass
class LocalizeOptions:
    logo: str | None = None
    logo_style: str = "ticker"           # ticker: turning green band with a running ticker | float: animated logo
                                         # | footer: the logo standing still at the bottom of the frame
    header_panels: list[str] | None = None  # images turned through on a 3D prism at the top
    header_hold: float = 4.0             # seconds per header panel when the source had no banner rhythm
    brand: str | None = None             # ticker text, e.g. the brand name
    brand_url: str | None = None         # ticker text, e.g. the site
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


def _banner_beats(layers: dict) -> list[float]:
    ov = layers.get("overlay")
    if not ov:
        return []
    return [seg["start"] for seg in ov["timeline"] if seg["kind"] == "state" and seg["start"] > 0.5]


def _header(proj: Path, opt: "LocalizeOptions", layers: dict, W: int, H: int, D: float) -> dict:
    """Copy the header panels into the project and time their turns: on the original
    banner's changes when there was one, otherwise every `header_hold` seconds."""
    import cv2

    panels, aspects = [], []
    for i, src in enumerate(opt.header_panels, 1):
        src = Path(src)
        img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise SystemExit(f"cannot read header panel {src}")
        dst = proj / "assets" / f"header_{i}{src.suffix.lower()}"
        dst.write_bytes(src.read_bytes())
        panels.append(f"assets/{dst.name}")
        aspects.append(img.shape[1] / img.shape[0])
    ov = layers.get("overlay")
    zone = ov["zone_px"] if ov else [0, int(H * 0.04), W, int(H * 0.27)]
    beats = _banner_beats(layers)
    if not beats:
        hold = max(1.5, opt.header_hold)
        beats = [round(t, 3) for t in _frange(hold, D - 1.0, hold)]
    # the widest panel sets the face shape: the others are cropped top/bottom, never at the sides
    return {"panels": panels, "aspect": round(max(aspects), 4), "zone_px": zone, "beats": beats}


def _frange(a: float, b: float, step: float):
    t = a
    while t < b:
        yield t
        t += step


def restyle(project: str, opt: LocalizeOptions, log=None) -> dict:
    """Change only the brand block (style and ticker text) of a localized project and
    rewrite index.html; audio, captions and the dub stay as they are."""
    log = log or _log
    proj = Path(project)
    m = json.loads((proj / "elements.json").read_text())
    logo = m["layers"].get("logo")
    if not logo:
        raise SystemExit("this project has no logo yet: run `vidextract localize --logo ...` first")
    logo.update(style=opt.logo_style, brand=opt.brand, url=opt.brand_url)
    (proj / "elements.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
    compose.write_project(proj, m)
    log(f"logo restyled: {opt.logo_style}" + (f" ({opt.brand} / {opt.brand_url})" if opt.brand or opt.brand_url else ""))
    return m


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
        beats = [0.0] + _banner_beats(layers)
        layers["logo"] = {**prepared, "zone_px": zone, "beats": sorted(set(beats)), "style": opt.logo_style,
                          "brand": opt.brand, "url": opt.brand_url}
        parts = "mark + wordmark animated separately" if prepared["mark"] else "single piece"
        log(f"logo ({opt.logo_style}): {src.name} in zone y={zone[1]}..{zone[3]}, {parts}, {len(beats)} pop beats")

    if opt.header_panels:
        layers["header"] = _header(proj, opt, layers, W, H, D)
        hd = layers["header"]
        log(f"header: {len(hd['panels'])} panels, {len(hd['beats'])} turns, zone y={hd['zone_px'][1]}..{hd['zone_px'][3]}")

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
    if caps.get("kind") == "box":
        return _localize_boxes(proj, m, opt, log)
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


def _translate(proj: Path, phrases: list[dict], opt: "LocalizeOptions", log) -> dict[str, str]:
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
    return tr


def _norm(text: str) -> str:
    import re
    return " ".join(re.findall(r"[^\W_]+", text.lower()))


def _localize_boxes(proj: Path, m: dict, opt: "LocalizeOptions", log) -> dict:
    """Phrase captions in boxes: each box is translated on its own (it stays on screen
    exactly where and when the original was, covering it), and the speech is dubbed in
    runs of boxes so the voice flows over a whole sentence."""
    layers = m["layers"]
    D = m["source"]["duration"]
    caps = layers["captions"]
    boxes = caps["phrases"]
    phrases = []
    for i, b in enumerate(boxes):
        nxt = boxes[i + 1]["start"] if i + 1 < len(boxes) else None
        phrases.append({"id": b["id"], "start": b["start"], "end": b["end"], "src": b["text"], "slot_end": nxt,
                        "card": b.get("card", False)})
    notes = caps.get("notes") or []
    for n in notes:
        phrases.append({"id": n["id"], "start": n["start"], "end": n["end"], "src": n["text"], "slot_end": n["end"],
                        "note": True})
    tr = _translate(proj, phrases, opt, log)
    for p, b in zip(phrases, boxes + notes):
        p["text"] = b["text_out"] = tr.get(b["id"], "")
    tr_path = proj / f"translation.{opt.lang_code}.json"
    tr_path.write_text(json.dumps(phrases, ensure_ascii=False, indent=1))
    if opt.translate_only:
        log(f"wrote {tr_path} — review/edit it, then run again without --translate-only")
        return m

    if opt.dub:
        cuts = [s["start"] for s in m.get("shots", [])[1:]]
        groups: list[dict] = []
        for p in phrases:
            text = p["text"].strip()
            if not text or p.get("note"):
                continue   # notices are read, not spoken
            g = groups[-1] if groups else None
            if g is not None:
                last, now = _norm(g["pieces"][-1]), _norm(text)
                if now.startswith(last) and p["start"] - g["end"] < 0.45:
                    g["pieces"][-1] = text          # a box that builds up word by word: say the full line once
                    g["end"] = p["end"]
                    continue
                if now and any(now in _norm(x) for x in g["pieces"][-2:]) and p["start"] - g["end"] < 0.45:
                    g["end"] = max(g["end"], p["end"])   # a fragment or repeat of what was just said
                    continue
            joins = (g is not None and p["start"] - g["end"] < 0.45 and not p["card"] and not g["card"]
                     and not any(g["end"] <= c <= p["start"] + 1e-3 for c in cuts)
                     and len(" ".join(g["pieces"]).split()) + len(text.split()) <= 16)
            if joins:
                g["pieces"].append(text)
                g["end"] = p["end"]
            else:
                groups.append({"id": f"g{len(groups)}", "start": p["start"], "end": p["end"],
                               "pieces": [text], "card": p["card"]})
        for g in groups:
            g["text"] = " ".join(g.pop("pieces"))
        for i, g in enumerate(groups):
            g["slot_end"] = groups[i + 1]["start"] if i + 1 < len(groups) else None
        bed = None
        if opt.keep_ambience and layers.get("source_audio"):
            bed = _ambience_bed(proj / layers["source_audio"], phrases, D)
            log("ambience: original track kept between phrases, muted under speech")
        _, voice_rel = _dub(proj, groups, D, opt, log, bed)
        layers["audio"] = {"src": voice_rel, "label": f"{opt.lang} voice-over ({opt.tts})"}
        log(f"dub: {len(groups)} spoken runs over {len(boxes)} boxes")
    caps["lang"] = opt.lang_code
    m["lang"] = opt.lang_code
    (proj / "elements.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
    compose.write_project(proj, m)
    log(f"done: {len(boxes)} boxed captions → {proj / 'index.html'}")
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
        tts = CachedTTS(tts, proj / "assets" / "tts_cache", f"{tts.voice}|{tts.model}")
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
