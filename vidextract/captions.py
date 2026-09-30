"""Burned-in word captions: white glyphs with a dark outline, one word at a time.

Per frame (inside the caption band) we keep glyph components whose pixels
sit next to a dark outline. Consecutive frames are grouped into words by
comparing a scale-invariant thumbnail of the glyph mask, which also lets us
measure the pop-in scale curve of every word.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

NORM = (64, 20)


@dataclass
class CaptionFrame:
    frame: int
    bbox: tuple[int, int, int, int]      # x0, y0, x1, y1 in work-frame pixels (glyph cores)
    glyph_h: float                       # median glyph height (≈ cap height)
    norm: np.ndarray                     # NORM-sized float mask, scale invariant
    core_png: bytes                      # glyph-core mask crop (for OCR)
    text_rgb: tuple[int, int, int]
    stroke_rgb: tuple[int, int, int]
    stroke_px: float


@dataclass
class Word:
    index: int
    start_frame: int
    end_frame: int                        # exclusive
    frames: list[CaptionFrame] = field(default_factory=list)
    text: str = ""
    confidence: float = 0.0

    @property
    def settled_h(self) -> float:
        """Resting size: the second half of a word's life is past any pop-in."""
        return float(np.median([f.glyph_h for f in self.frames[len(self.frames) // 2:]]))

    def pop_curve(self, max_frames: int = 15) -> list[float]:
        """Per-frame scale relative to the resting size, until it settles."""
        s = self.settled_h
        curve = [round(f.glyph_h / s, 3) for f in self.frames[:max_frames]]
        while len(curve) > 1 and abs(curve[-1] - 1) < 0.04 and abs(curve[-2] - 1) < 0.04:
            curve.pop()
        return curve if len(curve) > 1 or abs(curve[0] - 1) >= 0.04 else []


class CaptionDetector:
    def __init__(self, frame_w: int, frame_h: int, band: tuple[int, int]):
        self.W, self.H = frame_w, frame_h
        self.y0, self.y1 = band
        self.r = max(3, int(round(frame_h * 0.005)))
        self.k_near = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * self.r + 1, 2 * self.r + 1))
        self.k_pad = np.ones((3, 3), np.uint8)
        e = max(2, int(round(frame_h * 0.002)))
        self.k_edge = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * e + 1, 2 * e + 1))
        self.min_h = frame_h * 0.008
        self.max_h = frame_h * 0.09

    def detect(self, frame_idx: int, band_bgr: np.ndarray) -> tuple[CaptionFrame | None, np.ndarray | None]:
        """Returns caption info and a full text mask (core + outline) for the band."""
        hsv = cv2.cvtColor(band_bgr, cv2.COLOR_BGR2HSV)
        v, s = hsv[..., 2], hsv[..., 1]
        core = ((v >= 200) & (s <= 60)).astype(np.uint8)
        dark = (v <= 70).astype(np.uint8)
        n, lab, stats, cents = cv2.connectedComponentsWithStats(core, connectivity=8)
        if n <= 1:
            return None, None
        area = stats[:, cv2.CC_STAT_AREA].astype(np.float64)
        # a caption glyph is outlined all the way round: most of its boundary touches dark pixels
        boundary = (core > 0) & (cv2.erode(core, self.k_pad) == 0)
        near_dark = cv2.dilate(dark, self.k_edge) > 0
        per_b = np.bincount(lab[boundary], minlength=n)
        frac = np.bincount(lab[boundary & near_dark], minlength=n) / np.maximum(per_b, 1)
        hgt = stats[:, cv2.CC_STAT_HEIGHT]
        wid = stats[:, cv2.CC_STAT_WIDTH]
        ok = (frac > 0.75) & (area >= 12) & (hgt >= self.min_h) & (hgt <= self.max_h) & (wid < self.W * 0.5)
        ok[0] = False
        idx = np.nonzero(ok)[0]
        if len(idx) == 0:
            return None, None
        # keep glyphs on the dominant text line
        cy = cents[idx, 1]
        mh = np.median(hgt[idx])
        idx = idx[np.abs(cy - np.median(cy)) < mh * 0.9]
        if len(idx) == 0:
            return None, None
        keep = np.zeros(n, np.uint8)
        keep[idx] = 1
        glyphs = keep[lab]
        ys, xs = np.nonzero(glyphs)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        glyph_h = float(np.median(hgt[idx]))

        outline = (dark & cv2.dilate(glyphs, self.k_near)).astype(np.uint8)
        text_mask = cv2.dilate(glyphs | outline, self.k_pad)

        crop = glyphs[y0:y1, x0:x1]
        # shape fingerprint from the main text line only: diacritics (Ё, Й) and
        # descenders come and go with scale and would fake a word change
        tops = stats[idx, cv2.CC_STAT_TOP]
        line_t = int(np.median(tops))
        line_b = int(np.median(tops + hgt[idx]))
        line = glyphs[max(y0, line_t):max(line_t + 1, line_b), x0:x1]
        norm = cv2.resize(line.astype(np.float32), NORM, interpolation=cv2.INTER_AREA)
        ok_png, png = cv2.imencode(".png", crop * 255)

        core_px = band_bgr[glyphs > 0]
        ring = outline > 0
        stroke_px = 0.0
        if ring.any():
            dist = cv2.distanceTransform((1 - glyphs).astype(np.uint8), cv2.DIST_L2, 3)
            stroke_px = float(np.percentile(dist[ring], 85))
            ring_px = band_bgr[ring]
            stroke_rgb = tuple(int(c) for c in np.median(ring_px, axis=0)[::-1])
        else:
            stroke_rgb = (0, 0, 0)
        text_rgb = tuple(int(c) for c in np.median(core_px, axis=0)[::-1])
        cf = CaptionFrame(
            frame=frame_idx,
            bbox=(int(x0), int(y0 + self.y0), int(x1), int(y1 + self.y0)),
            glyph_h=glyph_h,
            norm=norm,
            core_png=png.tobytes(),
            text_rgb=text_rgb,
            stroke_rgb=stroke_rgb,
            stroke_px=stroke_px,
        )
        return cf, text_mask


def _aspect(cf: CaptionFrame) -> float:
    x0, _, x1, _ = cf.bbox
    return (x1 - x0) / max(1.0, cf.glyph_h)


def _norm_text(t: str) -> str:
    return t.upper().replace("Ё", "Е").replace("Й", "И")


def group_words(frames: list[CaptionFrame], fps: float) -> list[Word]:
    """Consecutive caption frames → words (split on gaps or on a shape change)."""
    words: list[Word] = []
    cur: Word | None = None
    prev: CaptionFrame | None = None
    for cf in frames:
        new = cur is None or prev is None or cf.frame != prev.frame + 1
        if not new:
            shape = float(np.abs(cf.norm - prev.norm).mean())
            ar = abs(np.log(_aspect(cf) / _aspect(prev)))
            # the same word popping in again: a word that had come to rest suddenly shrinks
            tail = [f.glyph_h for f in cur.frames[-4:]]
            rested = len(cur.frames) >= 8 and (max(tail) - min(tail)) <= 0.06 * np.mean(tail)
            restart = rested and cf.glyph_h < 0.8 * float(np.mean(tail))
            new = shape > 0.2 or ar > 0.18 or restart
        if new:
            cur = Word(index=len(words), start_frame=cf.frame, end_frame=cf.frame + 1)
            words.append(cur)
        cur.frames.append(cf)
        cur.end_frame = cf.frame + 1
        prev = cf
    # drop flicker (single frames) - they're detection noise
    min_len = max(2, int(0.06 * fps))
    words = [w for w in words if w.end_frame - w.start_frame >= min_len]
    for i, w in enumerate(words):
        w.index = i
    return words


def ocr_image(word: Word, target_h: int = 64) -> np.ndarray:
    """Black glyphs on white, the frame closest to the settled size."""
    settled = word.settled_h
    body = word.frames[len(word.frames) // 3:] or word.frames
    best = min(body, key=lambda f: abs(f.glyph_h - settled))
    crop = cv2.imdecode(np.frombuffer(best.core_png, np.uint8), cv2.IMREAD_GRAYSCALE)
    scale = target_h / max(1.0, best.glyph_h)
    crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    crop = 255 - (crop > 127).astype(np.uint8) * 255
    return cv2.copyMakeBorder(crop, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)


def merge_repeats(words: list[Word]) -> list[Word]:
    """Adjacent segments with the same recognised text are one word split by a wobble."""
    out: list[Word] = []
    for w in words:
        prev = out[-1] if out else None
        if (prev and w.text and _norm_text(w.text) == _norm_text(prev.text) and w.start_frame == prev.end_frame
                and w.frames[0].glyph_h >= 0.85 * prev.settled_h):
            if w.confidence > prev.confidence:
                prev.text = w.text
            prev.end_frame = w.end_frame
            prev.frames += w.frames
            prev.confidence = max(prev.confidence, w.confidence)
        else:
            out.append(w)
    for i, w in enumerate(out):
        w.index = i
    return out


def measure_style(words: list[Word], frame_w: int, frame_h: int, fps: float) -> dict:
    settled: list[CaptionFrame] = []
    for w in words:
        s = w.settled_h
        settled += [f for f in w.frames if abs(f.glyph_h - s) <= 0.06 * s]
    if not settled:
        settled = [f for w in words for f in w.frames]

    def med(vals):
        return float(np.median(vals)) if len(vals) else 0.0

    cx = med([(f.bbox[0] + f.bbox[2]) / 2 for f in settled]) / frame_w
    cy = med([(f.bbox[1] + f.bbox[3]) / 2 for f in settled]) / frame_h
    cap_h = med([f.glyph_h for f in settled])
    text_rgb = tuple(int(med([f.text_rgb[i] for f in settled])) for i in range(3))
    stroke_rgb = tuple(int(med([f.stroke_rgb[i] for f in settled])) for i in range(3))
    stroke = med([f.stroke_px for f in settled])

    # pop-in: glyph height of the first frames relative to the settled height
    curve: list[list[float]] = [[] for _ in range(10)]
    tail: list[list[float]] = [[] for _ in range(4)]
    for w in words:
        if len(w.frames) < 14:
            continue
        s = w.settled_h
        for i in range(10):
            curve[i].append(w.frames[i].glyph_h / s)
        for i in range(4):
            tail[i].append(w.frames[-1 - i].glyph_h / s)
    pop = [round(med(c), 3) for c in curve if c]
    # trim once the curve has settled
    while len(pop) > 1 and abs(pop[-1] - 1) < 0.04 and abs(pop[-2] - 1) < 0.04:
        pop.pop()
    exit_curve = [round(med(c), 3) for c in tail if c][::-1]
    has_exit = any(abs(x - 1) > 0.08 for x in exit_curve)

    def hexc(rgb):
        return "#{:02x}{:02x}{:02x}".format(*rgb)

    return {
        "center": {"x": round(cx, 4), "y": round(cy, 4)},
        "cap_height_px": round(cap_h, 1),
        "cap_height_frac": round(cap_h / frame_h, 5),
        "text_color": hexc(text_rgb),
        "stroke_color": hexc(stroke_rgb),
        "stroke_px": round(stroke, 1),
        "uppercase": _mostly_upper(words),
        "words_per_caption": 1,
        "pop_in": {"fps": fps, "scales": pop},
        "exit": {"scales": exit_curve if has_exit else [], "hard_cut": not has_exit},
    }


def _mostly_upper(words: list[Word]) -> bool:
    texts = [w.text for w in words if any(ch.isalpha() for ch in w.text)]
    return bool(texts) and sum(t == t.upper() for t in texts) >= 0.8 * len(texts)
