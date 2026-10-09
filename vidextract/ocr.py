"""OCR through tesseract.js (Node) — no system tesseract needed."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import cv2
import numpy as np

TESSDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/{lang}.traineddata"
HELPER = Path(__file__).parent / "js" / "ocr.mjs"


def cache_dir() -> Path:
    base = os.environ.get("VIDEXTRACT_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "vidextract")
    p = Path(base) / "tessdata"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_lang(lang: str) -> Path:
    d = cache_dir()
    for code in lang.split("+"):
        f = d / f"{code}.traineddata"
        if not f.exists():
            tmp = f.with_suffix(".part")
            with urllib.request.urlopen(TESSDATA_URL.format(lang=code), timeout=120) as r, open(tmp, "wb") as out:
                shutil.copyfileobj(r, out)
            tmp.rename(f)
    return d


def available() -> str | None:
    """Return a reason string if OCR can't run, else None."""
    if not shutil.which("node"):
        return "node not found"
    probe = subprocess.run(["node", "-e", "import('tesseract.js').then(()=>0)"], cwd=HELPER.parent,
                           capture_output=True, text=True)
    if probe.returncode != 0:
        return "tesseract.js not installed (run `npm install` in the repo)"
    return None


def recognize(images: list[np.ndarray], lang: str = "rus", psm: str = "7") -> list[dict]:
    """psm 7: one line of text per image; psm 6: a block of lines."""
    if not images:
        return []
    lang_path = ensure_lang(lang)
    with tempfile.TemporaryDirectory(prefix="vidextract-ocr-") as tmp:
        paths = []
        for i, img in enumerate(images):
            p = os.path.join(tmp, f"w{i:04d}.png")
            cv2.imwrite(p, img)
            paths.append(p)
        job = os.path.join(tmp, "job.json")
        with open(job, "w") as f:
            json.dump({"lang": lang, "langPath": str(lang_path), "psm": psm, "images": paths}, f)
        proc = subprocess.run(["node", str(HELPER), job], cwd=HELPER.parent, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"OCR failed: {proc.stderr[-2000:]}")
        results = json.loads(proc.stdout)
    for r in results:
        r["text"] = clean(r["text"])
    return results


def clean(text: str) -> str:
    text = text.replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" |_~`'\"‘’“”")
