"""Kokoro TTS worker (run with the Python that has `kokoro` installed).

Reads one JSON request per line on stdin ({"text", "voice", "speed", "out"}),
writes a 24 kHz WAV to "out" and answers {"ok": true} (or {"error": ...}) on
stdout. Kept running so the model loads once for a whole dub.
"""

import json
import os
import sys

# answers go out on a private copy of stdout; anything the libraries print (model
# downloads, warnings) is sent to stderr so it cannot garble an answer
_answers = os.fdopen(os.dup(1), "w")
os.dup2(2, 1)
sys.stdout = sys.stderr

import numpy as np
import soundfile as sf
from kokoro import KPipeline

pipes = {}
for line in sys.stdin:
    try:
        req = json.loads(line)
        voice = req["voice"]
        lang = voice[0] if voice[:1] in "abefhijpz" else "a"   # af_/am_ American, bf_/bm_ British, ...
        if lang not in pipes:
            pipes[lang] = KPipeline(lang_code=lang)
        chunks = [a for _, _, a in pipes[lang](req["text"], voice=voice, speed=float(req.get("speed", 1.0)))]
        audio = np.concatenate(chunks) if chunks else np.zeros(2400, np.float32)
        sf.write(req["out"], audio, 24000)
        _answers.write(json.dumps({"ok": True}) + "\n")
    except Exception as e:  # report and keep serving
        _answers.write(json.dumps({"error": f"{type(e).__name__}: {e}"}) + "\n")
    _answers.flush()
