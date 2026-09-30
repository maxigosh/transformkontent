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
The source is a transcript recovered from on-screen captions: one word per caption, no punctuation,
occasional OCR slips (Е/Ё, Й/И). Translate the meaning, not word for word, into natural spoken {lang}
that fits the speaker's register (stand-up comedy stays casual; keep jokes, swearing and tone).
Each phrase has a time slot: keep the translation speakable within it (about 2.7 words per second).
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
        lines.append(f'{p["id"]} [{slot:.1f}s]: {p["src"]}')
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
