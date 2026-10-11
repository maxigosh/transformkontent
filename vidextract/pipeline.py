"""One localization job, start to finish: fetch the video (a file or a link to a
social network), analyse it, translate (optionally stopping for a review of the
translation), dub, render and compress. Used by the web interface; every step
runs as a subprocess so its output lands in the job's log.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# language code → (name for the translator and the dub, tesseract model for reading captions)
LANGUAGES = {
    "ru": ("Russian", "rus"), "en": ("English", "eng"), "es": ("Spanish", "spa"), "pt": ("Portuguese", "por"),
    "de": ("German", "deu"), "fr": ("French", "fra"), "it": ("Italian", "ita"), "uk": ("Ukrainian", "ukr"),
    "tr": ("Turkish", "tur"), "pl": ("Polish", "pol"), "kk": ("Kazakh", "kaz"), "id": ("Indonesian", "ind"),
    "ar": ("Arabic", "ara"), "hi": ("Hindi", "hin"), "zh": ("Chinese", "chi_sim"), "ja": ("Japanese", "jpn"),
}


@dataclass
class JobOptions:
    url: str = ""                    # a link (Facebook, Instagram, TikTok, YouTube, ...) or empty for an upload
    source_lang: str = "ru"
    target_lang: str = "en"
    captions: str = "auto"           # auto | boxes | outline
    tts: str = "voicestudio"         # voicestudio | elevenlabs | none (subtitles only)
    voice: str = ""
    keep_ambience: bool = False
    header: str = "none"             # none | notify | ticker | float
    header_texts: list[str] = field(default_factory=list)
    footer_logo: bool = False
    brand: str = ""
    brand_url: str = ""
    review_translation: bool = False  # stop after translating so it can be edited


@dataclass
class Job:
    id: str
    options: JobOptions
    status: str = "queued"           # queued | running | review | done | failed
    step: str = ""
    created: float = field(default_factory=time.time)
    error: str = ""
    source_name: str = ""

    @property
    def dir(self) -> Path:
        return jobs_dir() / self.id

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "job.json").write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))

    @classmethod
    def load(cls, d: Path) -> "Job":
        data = json.loads((d / "job.json").read_text())
        data["options"] = JobOptions(**data["options"])
        return cls(**data)

    def log(self, msg: str) -> None:
        with open(self.dir / "job.log", "a", encoding="utf-8") as f:
            f.write(msg.rstrip("\n") + "\n")


def data_dir() -> Path:
    d = Path(os.environ.get("VIDEXTRACT_DATA") or Path.home() / ".local" / "share" / "vidextract")
    d.mkdir(parents=True, exist_ok=True)
    return d


def jobs_dir() -> Path:
    d = data_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- settings (API keys stay on the server, readable only by its owner) ------------------------
SETTING_KEYS = ("elevenlabs_api_key", "voicestudio_url", "voicestudio_api_key", "anthropic_api_key",
                "deepgram_api_key", "render_workers", "translator")


def load_settings() -> dict:
    f = data_dir() / "settings.json"
    s = json.loads(f.read_text()) if f.exists() else {}
    s.setdefault("voicestudio_url", "http://127.0.0.1:3900")
    s.setdefault("render_workers", 2)
    s.setdefault("translator", "free")
    if not s.get("voicestudio_api_key"):
        s["voicestudio_api_key"] = _voicestudio_key_from_docker()
    return s


def save_settings(new: dict) -> dict:
    s = load_settings()
    for k in SETTING_KEYS:
        if k in new and new[k] is not None and new[k] != "••••":
            s[k] = new[k]
    f = data_dir() / "settings.json"
    f.write_text(json.dumps(s, indent=1))
    os.chmod(f, 0o600)
    return s


def _voicestudio_key_from_docker() -> str:
    if not shutil.which("docker"):
        return ""
    try:
        r = subprocess.run(["docker", "exec", os.environ.get("VOICESTUDIO_CONTAINER", "voicestudio"),
                            "printenv", "OMNIVOICE_API_KEY"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


def step_env(settings: dict) -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{ROOT / '.bin'}:{ROOT / 'node_modules' / '.bin'}:{env.get('PATH', '')}"
    env["HYPERFRAMES_NO_TELEMETRY"] = env["HYPERFRAMES_NO_UPDATE_CHECK"] = "1"
    ffmpeg, ffprobe = ROOT / ".bin" / "ffmpeg", ROOT / ".bin" / "ffprobe"
    if ffmpeg.exists():
        env["HYPERFRAMES_FFMPEG_PATH"] = str(ffmpeg)
    if ffprobe.exists():
        env["HYPERFRAMES_FFPROBE_PATH"] = str(ffprobe)
    for key, var in (("elevenlabs_api_key", "ELEVENLABS_API_KEY"), ("voicestudio_url", "VOICESTUDIO_URL"),
                     ("voicestudio_api_key", "VOICESTUDIO_API_KEY"), ("anthropic_api_key", "ANTHROPIC_API_KEY")):
        if settings.get(key):
            env[var] = str(settings[key])
    return env


# ---- the job ------------------------------------------------------------------------------------
def _run(job: Job, cmd: list[str], env: dict, cwd: Path | None = None) -> None:
    job.log("$ " + " ".join(str(c) for c in cmd))
    with open(job.dir / "job.log", "a", encoding="utf-8") as out:
        p = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        raise RuntimeError(f"{Path(str(cmd[0])).name} {cmd[1] if len(cmd) > 1 else ''} exited with {p.returncode} "
                           "(see the log)")


def _step(job: Job, name: str) -> None:
    job.step = name
    job.save()
    job.log(f"\n==> {name}")


def run(job: Job) -> None:
    """Run (or resume after a translation review) one job; status lands in job.json."""
    o = job.options
    s = load_settings()
    env = step_env(s)
    py = sys.executable
    d = job.dir
    work = d / "work"
    lang_name, _ = LANGUAGES.get(o.target_lang, (o.target_lang, o.target_lang))
    _, tess = LANGUAGES.get(o.source_lang, (o.source_lang, "eng"))
    job.status, job.error = "running", ""
    job.save()
    try:
        src = next((p for p in d.glob("source.*")), None)
        if src is None:
            _step(job, "downloading")
            _run(job, [py, "-m", "yt_dlp", "--no-playlist", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
                       "--ffmpeg-location", str(ROOT / ".bin" / "ffmpeg"), "-o", str(d / "source.%(ext)s"), o.url], env)
            src = next(d.glob("source.*"))

        if not (work / "elements.json").exists():
            kind = o.captions
            if kind == "auto":
                from .boxcaps import probe_kind
                kind = probe_kind(str(src))
                job.log(f"captions look like: {kind}")
            _step(job, "analysing the video")
            cmd = [py, "-m", "vidextract", src, "-o", work, "--ocr-lang", tess]
            if kind == "boxes":
                cmd.append("--caption-boxes")
            _run(job, cmd, env, cwd=ROOT)

        edited = d / "translation.edited.json"
        loc = [py, "-m", "vidextract", "localize", work, "--lang", lang_name, "--lang-code", o.target_lang,
               "--source-lang", o.source_lang, "--translator", s.get("translator", "free")]
        logo = next((p for p in d.glob("logo.*")), None)
        if logo is not None:
            # one logo layer: animated at the top (ticker/float), or still at the bottom, or only
            # lending its mark to the notification card ("none")
            if o.header in ("ticker", "float"):
                style = o.header
            elif o.footer_logo:
                style = "footer"
            else:
                style = "none"
            if style != "none" or o.header == "notify":
                loc += ["--logo", logo, "--logo-style", style]
        if o.brand:
            loc += ["--brand", o.brand]
        if o.brand_url:
            loc += ["--brand-url", o.brand_url]
        if o.header == "notify" and o.header_texts:
            (d / "header.txt").write_text("\n".join(o.header_texts))
            loc += ["--header-texts", d / "header.txt"]
        if edited.exists():
            loc += ["--translation", edited]

        if o.review_translation and not edited.exists():
            _step(job, "translating")
            _run(job, loc + ["--translate-only", "--tts", "mock"], env, cwd=ROOT)
            job.status, job.step = "review", "check the translation"
            job.save()
            return

        _step(job, "translating and dubbing" if o.tts != "none" else "translating")
        if o.tts == "none":
            loc.append("--subtitles-only")
        else:
            loc += ["--tts", o.tts]
            if o.voice:
                loc += ["--voice", o.voice]
            if not o.keep_ambience:
                loc.append("--no-ambience")
        _run(job, loc, env, cwd=ROOT)

        _step(job, "rendering")
        out = d / "result.mp4"
        _run(job, ["hyperframes", "lint", "."], env, cwd=work)
        _run(job, ["hyperframes", "render", "-o", out, "--no-browser-gpu", "--workers", str(s.get("render_workers", 2))],
             env, cwd=work)

        _step(job, "compressing")
        ff = str(ROOT / ".bin" / "ffmpeg") if (ROOT / ".bin" / "ffmpeg").exists() else "ffmpeg"
        _run(job, [ff, "-v", "error", "-y", "-i", out, "-vf", "scale=720:-2", "-c:v", "libx264", "-crf", "26",
                   "-preset", "slow", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", d / "result_small.mp4"],
             env)
        job.status, job.step = "done", "done"
    except Exception as e:  # the job's log has the details
        job.status, job.error = "failed", str(e)
        job.log(f"error: {e}")
    job.save()
