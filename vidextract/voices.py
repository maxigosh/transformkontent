"""Pick a voice for the character before dubbing (VoiceStudio).

Without a voice profile VoiceStudio's "default" voice is drawn anew for every
phrase, so a dub changes speaker from line to line. A *design* profile is made
from a description ("middle-aged man, low pitch, calm"): VoiceStudio renders a
fixed reference sample for it once, and every phrase spoken with that profile
has the same voice.

    python -m vidextract voices --describe "middle-aged man, low pitch, calm" \\
        --describe "middle-aged man, deep voice, american accent" --name doctor

creates doctor_1, doctor_2, ... (or reuses them), speaks a sample line with
each into out/voices/, and prints the names to pass as --voice / VOICE=.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_TEXT = ("Today I'll tell you about three myths that keep you from recovering. "
                "The higher the dose, the faster it works? Wrong.")


class VoiceStudio:
    def __init__(self, url: str | None = None, api_key: str | None = None):
        self.url = (url or os.environ.get("VOICESTUDIO_URL") or "http://127.0.0.1:3900").rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("VOICESTUDIO_API_KEY", "").strip()

    def _call(self, method: str, path: str, body: bytes | None = None, ctype: str | None = None,
              timeout: float = 900) -> bytes:
        headers = {}
        if ctype:
            headers["Content-Type"] = ctype
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"VoiceStudio {method} {path}: {e.code} {e.read().decode(errors='replace')[:400]}") from None

    def json(self, method: str, path: str, payload: dict | None = None) -> dict | list:
        body = json.dumps(payload).encode() if payload is not None else None
        return json.loads(self._call(method, path, body, "application/json" if body else None))

    def form(self, path: str, fields: dict[str, str]) -> dict:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
        body = ("".join(parts) + f"--{boundary}--\r\n").encode()
        return json.loads(self._call("POST", path, body, f"multipart/form-data; boundary={boundary}"))

    def profiles(self) -> list[dict]:
        data = self.json("GET", "/profiles")
        return data if isinstance(data, list) else data.get("profiles", [])

    def design(self, name: str, description: str) -> dict:
        """A design profile for the description (reused if one with this name exists)."""
        for p in self.profiles():
            if p.get("name") == name:
                return p
        d = self.json("POST", "/design/describe", {"description": description})
        if not d.get("instruct"):
            raise RuntimeError(f"VoiceStudio understood nothing in {description!r} "
                               f"(try words like male, middle-aged, low pitch, american accent)")
        return self.form("/profiles", {"name": name, "kind": "design", "language": "Auto",
                                       "vd_states": json.dumps(d["attrs"]), "instruct": d["instruct"]})

    def speak(self, text: str, voice: str, model: str = "omnivoice") -> bytes:
        body = {"model": model, "input": text, "voice": voice, "response_format": "wav", "language": "en"}
        return self._call("POST", "/v1/audio/speech", json.dumps(body).encode(), "application/json")


def audition(descriptions: list[str], name: str = "voice", text: str = DEFAULT_TEXT,
             out: str | Path = "out/voices", log=print) -> list[dict]:
    vs = VoiceStudio()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for k, desc in enumerate(descriptions, 1):
        pname = f"{name}_{k}" if len(descriptions) > 1 else name
        prof = vs.design(pname, desc)
        pid = prof.get("id") or prof.get("profile_id") or pname
        log(f"{pname}: profile {pid} — {prof.get('instruct') or desc}")
        wav = out / f"{pname}.wav"
        wav.write_bytes(vs.speak(text, pid))
        log(f"   sample → {wav}")
        made.append({"name": pname, "id": pid, "description": desc, "sample": str(wav)})
    return made
