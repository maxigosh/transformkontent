"""End-to-end extraction: video → layers → HyperFrames project."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import compose, ocr
from .captions import CaptionDetector, group_words, measure_style, merge_repeats, ocr_image
from .layout import scan
from .media import Encoder, even, extract_audio, probe, read_frames, read_frames_at
from .overlay import OverlayMatter, segment_states, signature
from .plate import PlateCleaner


@dataclass
class Options:
    width: int = 1080
    ocr_lang: str = "rus"
    ocr: bool = True
    overlay_zone: tuple[float, float] | None = None   # force (top, bottom) fractions
    caption_band: tuple[float, float] | None = None
    no_overlay: bool = False
    no_captions: bool = False


def _log(msg: str) -> None:
    print(f"[vidextract] {msg}", flush=True)


def run(src: str, out_dir: str, opt: Options = Options(), log=_log) -> dict:
    t0 = time.time()
    out = Path(out_dir)
    assets = out / "assets"
    (assets / "overlay_states").mkdir(parents=True, exist_ok=True)
    (assets / "shots").mkdir(parents=True, exist_ok=True)

    info = probe(src)
    W = even(min(opt.width, info.width))
    H = even(W * info.height / info.width)
    fps = info.fps
    log(f"source {info.width}x{info.height} {fps:g}fps {info.duration:.2f}s ({info.video_codec}); working at {W}x{H}")

    # ---- pass A: layout scan -------------------------------------------------
    log("pass A: scanning layout (overlay zone, caption band, cuts)…")
    layout = scan(info, progress=lambda n: None)
    n_frames = layout.n_frames
    oz = None if opt.no_overlay else (opt.overlay_zone or layout.overlay_zone)
    cb = None if opt.no_captions else (opt.caption_band or layout.caption_band)
    log(f"  overlay zone: {oz and tuple(round(v, 3) for v in oz)} (hue {layout.overlay_hue}), "
        f"caption band: {cb and tuple(round(v, 3) for v in cb)}, cuts at "
        f"{[round(c / fps, 2) for c in layout.cuts]}")

    bounds = [0] + layout.cuts + [n_frames]
    shots = [{"index": i, "start_frame": a, "end_frame": b, "start": round(a / fps, 3), "end": round(b / fps, 3),
              "thumb": f"assets/shots/shot_{i + 1:02d}.jpg"} for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]
    thumb_at = {(s["start_frame"] + s["end_frame"]) // 2: s for s in shots}

    # ---- pass B: per-frame layer separation ---------------------------------
    z0 = z1 = c0 = c1 = 0
    matter = det = ov_enc = None
    if oz:
        z0, z1 = even(oz[0] * H) if oz[0] > 0 else 0, min(H, even(oz[1] * H))
        matter = OverlayMatter(layout.overlay_hue if layout.overlay_hue is not None else 120, H)
        ov_enc = Encoder(assets / "overlay.webm", W, z1 - z0, fps, "vp9a", "bgra")
    if cb:
        c0, c1 = int(cb[0] * H), int(np.ceil(cb[1] * H))
        det = CaptionDetector(W, H, (c0, c1))
    cleaner = PlateCleaner(H, fps, layout.cuts)
    plate_enc = Encoder(assets / "plate.mp4", W, H, fps, "h264", "bgr24")

    def write_plate(j: int, clean: np.ndarray) -> None:
        plate_enc.write(clean)
        if j in thumb_at:
            cv2.imwrite(str(out / thumb_at[j]["thumb"]), cv2.resize(clean, (W // 3, H // 3)),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])

    log("pass B: separating layers (overlay matte, captions, clean plate)…")
    sigs, cap_frames = [], []
    tick = time.time()
    for i, frame in enumerate(read_frames(src, W, H)):
        ov = cap = None
        if matter is not None:
            zone = frame[z0:z1]
            alpha = matter.matte(zone)
            ov_enc.write(np.dstack([zone, alpha]))
            sigs.append(signature(zone, alpha))
            ov = (z0, np.maximum(alpha, matter.specks(zone)))
        if det is not None:
            cf, mask = det.detect(i, frame[c0:c1])
            if cf is not None:
                cap_frames.append(cf)
                cap = (c0, mask)
        for j, clean in cleaner.push(i, frame, ov, cap, (c0, c1) if det is not None else None):
            write_plate(j, clean)
        if time.time() - tick > 10:
            tick = time.time()
            log(f"  frame {i}/{n_frames}")
    for j, clean in cleaner.flush():
        write_plate(j, clean)
    plate_enc.close()
    if ov_enc:
        ov_enc.close()

    if info.has_audio:
        extract_audio(src, assets / "audio.m4a")

    # ---- overlay states --------------------------------------------------------
    overlay = None
    if matter is not None and sigs:
        states, timeline = segment_states(np.stack(sigs), fps)
        reps = read_frames_at(str(assets / "overlay.webm"), [s.rep_frame for s in states], W, z1 - z0, alpha=True)
        st_out = []
        for s in states:
            entry = {"id": s.id, "coverage": round(s.coverage, 4),
                     "appearances": [{"start": round(a / fps, 3), "end": round(b / fps, 3)} for a, b in s.frames]}
            img = reps.get(s.rep_frame)
            if img is not None and img[..., 3].max() > 0:
                ys, xs = np.nonzero(img[..., 3] > 8)
                x0_, x1_, y0_, y1_ = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
                png = f"assets/overlay_states/{s.id}.png"
                cv2.imwrite(str(out / png), img[y0_:y1_, x0_:x1_])
                entry.update({"png": png, "bbox": [int(x0_), int(y0_ + z0), int(x1_), int(y1_ + z0)]})
            st_out.append(entry)
        overlay = {"src": "assets/overlay.webm", "zone_px": [0, z0, W, z1], "hue": layout.overlay_hue,
                   "states": st_out, "timeline": timeline}
        log(f"overlay: {len([s for s in st_out if 'png' in s])} distinct states, "
            f"{sum(1 for t in timeline if t['kind'] == 'transition')} transitions")

    # ---- captions ----------------------------------------------------------------
    captions = None
    if det is not None and cap_frames:
        words = group_words(cap_frames, fps)
        log(f"captions: {len(words)} word segments")
        if opt.ocr:
            reason = ocr.available()
            if reason:
                log(f"  OCR skipped: {reason}")
            else:
                log(f"  OCR ({opt.ocr_lang})…")
                res = ocr.recognize([ocr_image(w) for w in words], opt.ocr_lang)
                for w, r in zip(words, res):
                    w.text, w.confidence = r["text"], r["confidence"]
                words = merge_repeats(words)
        style = measure_style(words, W, H, fps)
        if style["uppercase"]:  # OCR sometimes reads caps as lowercase (это/ЭТО look alike)
            for w in words:
                w.text = w.text.upper()
        captions = {
            "style": style,
            "words": [{
                "id": f"w{w.index}",
                "text": w.text or f"#{w.index}",
                "start": round(w.start_frame / fps, 3),
                "end": round(w.end_frame / fps, 3),
                "confidence": round(w.confidence, 1),
                "pop": w.pop_curve(),
                "bbox": list(max(w.frames, key=lambda f: f.glyph_h).bbox),
            } for w in words],
        }
        (out / "captions.json").write_text(json.dumps(
            [{"id": w["id"], "text": w["text"], "start": w["start"], "end": w["end"]} for w in captions["words"]],
            ensure_ascii=False, indent=1))

    manifest = {
        "generator": "vidextract",
        "source": {"path": str(src), "width": info.width, "height": info.height, "fps": fps,
                   "duration": round(info.duration, 3), "frames": n_frames, "codec": info.video_codec},
        "canvas": {"width": W, "height": H},
        "layers": {
            "plate": {"src": "assets/plate.mp4", "note": "footage with overlay + captions inpainted"},
            "overlay": overlay,
            "captions": captions,
            "audio": {"src": "assets/audio.m4a"} if info.has_audio else None,
        },
        "shots": shots,
    }
    (out / "elements.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    compose.write_project(out, manifest)
    log(f"done in {time.time() - t0:.0f}s → {out}")
    return manifest
