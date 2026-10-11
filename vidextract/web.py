"""Web interface: upload a video or paste a link, pick the languages and the voice,
add a logo, and get the localized video back.

    python -m vidextract serve                      # http://127.0.0.1:8090
    python -m vidextract serve --host 0.0.0.0 --password secret

Jobs run one at a time in a background thread (a render takes the whole CPU);
everything a job needs and makes lives in its own folder under the data dir.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import threading
import urllib.request
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import pipeline
from .pipeline import Job, JobOptions, LANGUAGES, data_dir, jobs_dir, load_settings, save_settings
from .voices import DEFAULT_TEXT, VoiceStudio

HERE = Path(__file__).parent
_queue: "queue.Queue[str]" = queue.Queue()


def _worker() -> None:
    while True:
        job_id = _queue.get()
        try:
            pipeline.run(Job.load(jobs_dir() / job_id))
        except Exception as e:  # never let the worker die
            print(f"job {job_id}: {e}", flush=True)


def _all_jobs() -> list[Job]:
    jobs = []
    for d in jobs_dir().iterdir():
        if (d / "job.json").exists():
            try:
                jobs.append(Job.load(d))
            except Exception:
                pass
    return sorted(jobs, key=lambda j: -j.created)


def _job(job_id: str) -> Job:
    d = jobs_dir() / job_id
    if not (job_id.isalnum() and (d / "job.json").exists()):
        raise HTTPException(404, "no such job")
    return Job.load(d)


def _view(j: Job) -> dict:
    return {"id": j.id, "status": j.status, "step": j.step, "error": j.error, "created": j.created,
            "source": j.source_name or j.options.url, "options": j.options.__dict__,
            "result": (j.dir / "result_small.mp4").exists()}


def _vs() -> VoiceStudio:
    s = load_settings()
    return VoiceStudio(s.get("voicestudio_url"), s.get("voicestudio_api_key") or "")


def create_app(password: str = "") -> FastAPI:
    app = FastAPI(title="vidextract")
    for j in _all_jobs():   # pick up jobs a restart interrupted
        if j.status in ("queued", "running"):
            j.status = "queued"
            j.save()
            _queue.put(j.id)
    threading.Thread(target=_worker, daemon=True).start()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if password and request.url.path.startswith("/api/") and request.url.path != "/api/login":
            token = request.headers.get("x-token") or request.cookies.get("vx_token") or ""
            if token != hashlib.sha256(password.encode()).hexdigest():
                return JSONResponse({"detail": "login required"}, status_code=401)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (HERE / "static" / "index.html").read_text(encoding="utf-8")

    @app.post("/api/login")
    def login(data: dict):
        if not password:
            return {"token": ""}
        if data.get("password") != password:
            raise HTTPException(401, "wrong password")
        return {"token": hashlib.sha256(password.encode()).hexdigest()}

    @app.get("/api/config")
    def config():
        s = load_settings()
        masked = {k: ("••••" if v and k.endswith("_key") else v) for k, v in s.items()}
        return {"languages": {k: v[0] for k, v in LANGUAGES.items()}, "settings": masked,
                "auth": bool(password)}

    @app.post("/api/settings")
    def settings(data: dict):
        save_settings(data)
        return config()

    # ---- voices --------------------------------------------------------------------------
    @app.get("/api/voices")
    def voices(engine: str = "voicestudio"):
        s = load_settings()
        out = []
        if engine == "voicestudio":
            try:
                for p in _vs().profiles():
                    out.append({"id": p.get("id"), "name": p.get("name"), "note": p.get("instruct") or ""})
            except Exception as e:
                raise HTTPException(502, f"VoiceStudio: {e}")
        elif engine == "elevenlabs" and s.get("elevenlabs_api_key"):
            req = urllib.request.Request("https://api.elevenlabs.io/v1/voices",
                                         headers={"xi-api-key": s["elevenlabs_api_key"]})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                for v in data.get("voices", []):
                    labels = ", ".join(str(x) for x in (v.get("labels") or {}).values())
                    out.append({"id": v["voice_id"], "name": v["name"], "note": labels,
                                "preview": v.get("preview_url")})
            except Exception as e:
                raise HTTPException(502, f"ElevenLabs: {e}")
        return out

    @app.post("/api/voices/design")
    def design(data: dict):
        name, desc = (data.get("name") or "").strip(), (data.get("description") or "").strip()
        if not name or not desc:
            raise HTTPException(400, "a name and a description are needed")
        try:
            p = _vs().design(name, desc)
        except Exception as e:
            raise HTTPException(502, str(e))
        return {"id": p.get("id"), "name": p.get("name"), "note": p.get("instruct") or desc}

    @app.get("/api/voices/sample")
    def sample(voice: str, text: str = DEFAULT_TEXT):
        key = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:16]
        f = data_dir() / "samples" / f"{key}.wav"
        if not f.exists():
            f.parent.mkdir(exist_ok=True)
            try:
                f.write_bytes(_vs().speak(text, voice))
            except Exception as e:
                raise HTTPException(502, str(e))
        return FileResponse(f, media_type="audio/wav")

    # ---- jobs ----------------------------------------------------------------------------
    @app.get("/api/jobs")
    def jobs():
        return [_view(j) for j in _all_jobs()]

    @app.post("/api/jobs")
    async def create(options: str = Form(...), video: UploadFile | None = File(None),
                     logo: UploadFile | None = File(None), art: UploadFile | None = File(None)):
        opt = JobOptions(**{k: v for k, v in json.loads(options).items() if k in JobOptions.__dataclass_fields__})
        if not video and not opt.url.strip():
            raise HTTPException(400, "upload a video or paste a link")
        job = Job(id=uuid.uuid4().hex[:10], options=opt)
        job.dir.mkdir(parents=True)
        if video:
            ext = Path(video.filename or "v.mp4").suffix.lower() or ".mp4"
            with open(job.dir / f"source{ext}", "wb") as f:
                shutil.copyfileobj(video.file, f)
            job.source_name = video.filename or "video"
        for up, stem in ((logo, "logo"), (art, "art")):
            if up:
                ext = Path(up.filename or "x.png").suffix.lower() or ".png"
                with open(job.dir / f"{stem}{ext}", "wb") as f:
                    shutil.copyfileobj(up.file, f)
        job.save()
        _queue.put(job.id)
        return _view(job)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        j = _job(job_id)
        log = j.dir / "job.log"
        tail = log.read_text(encoding="utf-8", errors="replace")[-6000:] if log.exists() else ""
        return {**_view(j), "log": tail}

    @app.get("/api/jobs/{job_id}/translation")
    def translation(job_id: str):
        j = _job(job_id)
        f = next(iter(sorted((j.dir / "work").glob("translation.*.json"))), None)
        if f is None:
            raise HTTPException(404, "no translation yet")
        rows = json.loads(f.read_text())
        return [{"id": r["id"], "start": r.get("start"), "end": r.get("end"), "src": r.get("src", ""),
                 "text": r.get("text", "")} for r in rows]

    @app.post("/api/jobs/{job_id}/translation")
    def approve(job_id: str, rows: list[dict]):
        j = _job(job_id)
        (j.dir / "translation.edited.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        j.status = "queued"
        j.save()
        _queue.put(j.id)
        return _view(j)

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        j = _job(job_id)
        j.status = "queued"
        j.save()
        _queue.put(j.id)
        return _view(j)

    @app.delete("/api/jobs/{job_id}")
    def delete(job_id: str):
        j = _job(job_id)
        if j.status == "running":
            raise HTTPException(409, "the job is running")
        shutil.rmtree(j.dir)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/video")
    def video(job_id: str, full: bool = False):
        j = _job(job_id)
        f = j.dir / ("result.mp4" if full else "result_small.mp4")
        if not f.exists():
            raise HTTPException(404, "not rendered yet")
        name = f"{Path(j.source_name or j.id).stem}_{j.options.target_lang}{'' if not full else '_full'}.mp4"
        return FileResponse(f, media_type="video/mp4", filename=name)

    return app


def serve(host: str = "127.0.0.1", port: int = 8090, password: str = "") -> None:
    import uvicorn

    if host not in ("127.0.0.1", "localhost") and not password:
        raise SystemExit("listening beyond this machine needs --password (or keep the default host "
                         "and open it through an SSH tunnel)")
    uvicorn.run(create_app(password or os.environ.get("VIDEXTRACT_PASSWORD", "")), host=host, port=port,
                log_level="warning")
