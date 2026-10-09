"""Speech phrases from the extracted captions, and their translation.

Captions come out of `vidextract` one word at a time with timings, which is a
free word-level transcript. Words are regrouped into phrases at pauses and
shot cuts; each phrase keeps its time slot so the dub can be placed back on it.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"


def phrases_from_words(words: list[dict], cuts: list[float] = (), gap: float = 0.45, max_words: int = 14) -> list[dict]:
    phrases: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            phrases.append({
                "id": f"p{len(phrases)}",
                "start": cur[0]["start"],
                "end": cur[-1]["end"],
                "src": " ".join(w["text"] for w in cur),
            })
            cur.clear()

    for w in words:
        if cur:
            pause = w["start"] - cur[-1]["end"]
            crosses_cut = any(cur[-1]["end"] <= c <= w["start"] + 1e-3 for c in cuts)
            if pause >= gap or crosses_cut or len(cur) >= max_words:
                flush()
        cur.append(w)
    flush()
    # each phrase may use the time until the next one starts
    for i, p in enumerate(phrases):
        p["slot_end"] = phrases[i + 1]["start"] if i + 1 < len(phrases) else None
    return phrases


SYSTEM = """You translate the speech of a short vertical video for dubbing.
The source is a transcript recovered from on-screen captions (one word or one short phrase per caption,
little punctuation, occasional OCR slips such as Е/Ё, Й/И, № read as N). Translate the meaning, not word for
word, into natural spoken {lang} that fits the speaker's register (stand-up comedy stays casual with its jokes
and swearing; an expert explaining stays clear and confident).
Each phrase has a time slot: keep the translation speakable within it (about 2.7 words per second), and about
as long as the caption it replaces. Phrases marked [card] are big title cards: keep them as short (e.g. "Myth #1").
Phrases marked [note] are small on-screen notices such as a medical disclaimer: translate them as the standard
written notice in {lang}, not as speech.
Return exactly one translation per input phrase, same ids, same order."""


def translate_claude(phrases: list[dict], lang: str = "English", model: str = DEFAULT_MODEL) -> dict[str, str]:
    """Translate all phrases in one request so the model sees the whole monologue."""
    import anthropic
    from pydantic import BaseModel

    class Item(BaseModel):
        id: str
        text: str

    class Result(BaseModel):
        phrases: list[Item]

    lines = []
    for p in phrases:
        slot = (p["slot_end"] or p["end"] + 1.0) - p["start"]
        card = " [card]" if p.get("card") else (" [note]" if p.get("note") else "")
        lines.append(f'{p["id"]} [{slot:.1f}s]{card}: {p["src"]}')
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM.format(lang=lang),
        messages=[{"role": "user", "content": "Phrases:\n" + "\n".join(lines)}],
        output_format=Result,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("translation request was declined by the model")
    got = {item.id: item.text.strip() for item in response.parsed_output.phrases}
    missing = [p["id"] for p in phrases if p["id"] not in got]
    if missing:
        raise RuntimeError(f"translation is missing phrases: {missing}")
    return got


def load_translation(path: str | Path) -> dict[str, str]:
    """A JSON list of {"id", "text"} (or {"id", "en"}) — e.g. an edited translation.json."""
    data = json.loads(Path(path).read_text())
    return {d["id"]: (d.get("text") or d.get("en") or "").strip() for d in data}


# ---- free translation (no key, no account) -----------------------------------------------
GTX_URL = "https://translate.googleapis.com/translate_a/single"


def _gtx(text: str, sl: str, tl: str) -> str:
    """Google Translate's free web endpoint (the one browser extensions use)."""
    import time
    import urllib.parse
    import urllib.request

    body = urllib.parse.urlencode({"q": text}).encode()
    url = GTX_URL + "?" + urllib.parse.urlencode({"client": "gtx", "sl": sl, "tl": tl, "dt": "t"})
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    return ""


def _argos(texts: list[str], sl: str, tl: str) -> list[str]:
    """Offline fallback: Argos Translate (free, runs locally; ~100 MB model on first use)."""
    try:
        import argostranslate.package as pkg
        import argostranslate.translate as tr
    except ImportError:
        raise RuntimeError("Google Translate is unreachable and the offline fallback is not installed: "
                           "pip install argostranslate (or pass --translation file.json)") from None

    if not any(l.code == sl for l in tr.get_installed_languages()):
        pkg.update_package_index()
        p = next(p for p in pkg.get_available_packages() if p.from_code == sl and p.to_code == tl)
        pkg.install_from_path(p.download())
    return [tr.translate(t, sl, tl) for t in texts]


def translate_free(texts: list[str], sl: str = "ru", tl: str = "en", log=print) -> list[str]:
    """Translate a list of sentences for free: Google's web endpoint, or Argos offline
    when that is unreachable."""
    import time

    out: list[str] = []
    try:
        for t in texts:
            out.append(_gtx(t, sl, tl) if t.strip() else "")
            time.sleep(0.15)
        log(f"translation: {len(texts)} sentences via Google Translate (free endpoint)")
        return out
    except Exception as e:
        log(f"translation: Google unreachable ({e.__class__.__name__}), using Argos Translate offline")
        return _argos(texts, sl, tl)
