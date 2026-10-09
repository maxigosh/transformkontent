"""Phrase captions drawn as solid colour boxes (white text on a red box, say), and
full-frame title cards in the same colour.

Every frame: pixels of the box colour inside the caption area are grouped into
components; each one that is a near-solid rectangle is a box. Boxes are linked
across frames into segments by position and by the white text inside them, so a
new phrase in a box of the same size still starts a new segment, and a box that
slides across the frame stays one segment (its track is kept).

The box hides whatever is behind it, so localized captions are drawn as boxes of
the same colour that cover the original ones; nothing has to be inpainted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Box:
    frame: int
    x: int
    y: int
    w: int
    h: int
    sig: np.ndarray          # white text inside the box, 96x24 binary
    card: bool

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass(eq=False)
class Segment:
    index: int
    boxes: list[Box] = field(default_factory=list)
    text: str = ""
    confidence: float = 0.0

    @property
    def start(self) -> int:
        return self.boxes[0].frame

    @property
    def end(self) -> int:
        return self.boxes[-1].frame + 1

    @property
    def card(self) -> bool:
        return sum(b.card for b in self.boxes) * 2 > len(self.boxes)

    def rep(self) -> Box:
        """The frame in the middle of the segment's steady part."""
        steady = self.boxes[len(self.boxes) // 4: max(len(self.boxes) // 4 + 1, len(self.boxes) * 3 // 4)]
        return steady[len(steady) // 2]


def _sig_iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return 1.0 if union == 0 else inter / union


class BoxCaptionTracker:
    def __init__(self, W: int, H: int, hue_ranges=((0, 14), (172, 180)), band=(0.2, 0.96)):
        self.W, self.H = W, H
        self.hue_ranges = hue_ranges
        self.y0, self.y1 = int(band[0] * H), int(band[1] * H)
        self.segments: list[Segment] = []
        self.active: list[Segment] = []
        self.k_close = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, W // 140) | 1,) * 2)

    def _mask(self, hsv: np.ndarray) -> np.ndarray:
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        hue = np.zeros(h.shape, bool)
        for a, b in self.hue_ranges:
            hue |= (h >= a) & (h <= b)
        return (hue & (s > 150) & (v > 150)).astype(np.uint8)

    def _core(self, m: np.ndarray, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int] | None:
        """Tighten a component's bbox to its dense rectangle (drops red things touching it)."""
        sub = m[y:y + h, x:x + w].astype(np.float32)
        rows, cols = sub.mean(axis=1), sub.mean(axis=0)
        r = np.nonzero(rows > max(0.45, rows.max() * 0.6))[0]
        c = np.nonzero(cols > max(0.45, cols.max() * 0.6))[0]
        if len(r) < 4 or len(c) < 4:
            return None
        ya, yb, xa, xb = r[0], r[-1] + 1, c[0], c[-1] + 1
        if sub[ya:yb, xa:xb].mean() < 0.55:
            return None
        return x + xa, y + ya, xb - xa, yb - ya

    def detect(self, i: int, frame: np.ndarray) -> list[Box]:
        band = frame[self.y0:self.y1]
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        m = cv2.morphologyEx(self._mask(hsv), cv2.MORPH_CLOSE, self.k_close)
        n, _, st, _ = cv2.connectedComponentsWithStats(m, 8)
        out = []
        for k in range(1, n):
            x, y, w, h, a = (int(v) for v in st[k])
            if w < 0.07 * self.W or h < 0.022 * self.H or a < 0.5 * w * h:
                continue
            core = self._core(m, x, y, w, h)
            if core is None:
                continue
            x, y, w, h = (int(v) for v in core)
            white = (hsv[y:y + h, x:x + w, 2] > 185) & (hsv[y:y + h, x:x + w, 1] < 90)
            sig = cv2.resize(white.astype(np.uint8) * 255, (96, 24), interpolation=cv2.INTER_AREA) > 96
            card = bool(h > 0.25 * self.H)  # a title card, not a caption box
            out.append(Box(i, x, y + self.y0, w, h, sig, card))
        return out

    def push(self, i: int, frame: np.ndarray) -> None:
        boxes = self.detect(i, frame)
        still = []
        for b in boxes:
            best, score = None, 0.0
            for s in self.active:
                p = s.boxes[-1]
                if b.frame - p.frame > 3 or p.card != b.card:
                    continue
                size = min(b.w, p.w) / max(b.w, p.w) * min(b.h, p.h) / max(b.h, p.h)
                near = abs(b.cx - p.cx) < 0.06 * self.W and abs(b.cy - p.cy) < 0.06 * self.H
                same_text = _sig_iou(b.sig, p.sig)
                # same phrase: the box may slide or settle, but its text stays the same
                if (near and size > 0.8 and same_text > 0.6) or (size > 0.9 and same_text > 0.72):
                    sc = same_text + size
                    if sc > score:
                        best, score = s, sc
            if best is None:
                best = Segment(len(self.segments))
                self.segments.append(best)
            if best not in still:
                best.boxes.append(b)
                still.append(best)
        keep = self.active + [s for s in still if s not in self.active]
        self.active = [s for s in keep if i - s.boxes[-1].frame <= 3]

    def finish(self, fps: float, min_s: float = 0.2) -> list[Segment]:
        segs = [s for s in self.segments if (s.end - s.start) >= min_s * fps and len(s.boxes) >= 3]
        segs.sort(key=lambda s: s.start)
        for k, s in enumerate(segs):
            s.index = k
        return segs


def _merge_pieces(segs: list[Segment], H: int) -> list[Segment]:
    """A box that splits into pieces side by side (an animated reveal or a tear) is one
    caption: pieces on the same row that are on screen together become one segment whose
    box is their union, so the cover hides all of them."""
    out: list[Segment] = []
    for s in segs:
        host = None
        for o in out:
            if o.card or s.card:
                continue
            overlap = min(o.end, s.end) - max(o.start, s.start)
            if overlap >= 0.5 * min(o.end - o.start, s.end - s.start) and abs(o.rep().cy - s.rep().cy) < 0.03 * H:
                host = o
                break
        if host is None:
            out.append(s)
            continue
        by = {b.frame: b for b in host.boxes}
        for b in s.boxes:
            a = by.get(b.frame)
            if a is None:
                by[b.frame] = b
                continue
            x0, y0 = min(a.x, b.x), min(a.y, b.y)
            x1, y1 = max(a.x + a.w, b.x + b.w), max(a.y + a.h, b.y + b.h)
            by[b.frame] = Box(b.frame, x0, y0, x1 - x0, y1 - y0, a.sig | b.sig, False)
        host.boxes = [by[f] for f in sorted(by)]
    for k, s in enumerate(out):
        s.index = k
    return out


def track_of(seg: Segment, fps: float, pad: int) -> list[list[float]]:
    """Keyframes [t, cx, cy, w, h] that keep a cover box over the original one.

    A box that stays put gets one keyframe with its largest steady size; one that
    moves gets a keyframe per frame that covers it and its neighbours."""
    b = seg.boxes
    cx = np.array([x.cx for x in b])
    cy = np.array([x.cy for x in b])
    w = np.array([x.w for x in b], float)
    h = np.array([x.h for x in b], float)
    t0 = seg.start / fps
    if np.ptp(cx) < 12 and np.ptp(cy) < 12:
        return [[round(t0, 3), round(float(np.median(cx)), 1), round(float(np.median(cy)), 1),
                 round(float(np.percentile(w, 90)) + 2 * pad, 1), round(float(np.percentile(h, 90)) + 2 * pad, 1)]]
    # moving (or torn) boxes: cover the union of the rectangles over neighbouring frames, so
    # a piece the detector missed for a frame, or the box sliding on, never peeks out
    x0 = np.array([x.x for x in b], float)
    y0 = np.array([x.y for x in b], float)
    x1, y1 = x0 + w, y0 + h
    r = 2
    keys, last = [], None
    for j, x in enumerate(b):
        lo, hi = max(0, j - r), j + r + 1
        ax, ay, bx, by = x0[lo:hi].min(), y0[lo:hi].min(), x1[lo:hi].max(), y1[lo:hi].max()
        cur = [round(x.frame / fps, 3), round((ax + bx) / 2, 1), round((ay + by) / 2, 1),
               round(bx - ax + 2 * pad, 1), round(by - ay + 2 * pad, 1)]
        if last is None or max(abs(cur[i] - last[i]) for i in range(1, 5)) > 2:
            keys.append(cur)
            last = cur
    return keys


def ocr_crop(frame: np.ndarray, box: Box, inset: int = 2) -> np.ndarray:
    """White text of the box as black-on-white, scaled for tesseract."""
    crop = frame[box.y + inset: box.y + box.h - inset, box.x + inset: box.x + box.w - inset]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    white = (hsv[..., 2] > 170) & (hsv[..., 1] < 110)
    img = np.where(white, 0, 255).astype(np.uint8)
    return cv2.copyMakeBorder(img, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)


def measure_box_style(segs: list[Segment], frames: dict[int, np.ndarray], W: int, H: int) -> dict:
    """Box colour, text colour and the text size (cap height) of ordinary caption boxes."""
    colours, caps, centers = [], [], []
    for s in segs:
        if s.card:
            continue
        b = s.rep()
        img = frames.get(b.frame)
        if img is None:
            continue
        crop = img[b.y + 3: b.y + b.h - 3, b.x + 3: b.x + b.w - 3]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        red = (hsv[..., 1] > 215) & (hsv[..., 2] > 170)   # the solid part: boxes may be a little see-through
        if red.any():
            colours.append(np.median(crop[red], axis=0))
        white = (hsv[..., 2] > 185) & (hsv[..., 1] < 90)
        n_lines = len(_runs(white.mean(axis=1) > 0.02, gap=max(3, b.h // 12)))
        if n_lines:
            caps.append(b.h / n_lines)          # line pitch inside the box
        centers.append(b.cy)
    bgr = np.median(colours, axis=0) if colours else np.array([11, 10, 224])
    pitch = float(np.median(caps)) if caps else 0.055 * H
    return {
        "box_color": "#{:02x}{:02x}{:02x}".format(int(bgr[2]), int(bgr[1]), int(bgr[0])),
        "text_color": "#ffffff",
        # a line of text in these boxes takes ~1.6 font sizes (padding included)
        "font_px": round(pitch / 1.6, 1),
        "center_y": round(float(np.median(centers)) if centers else 0.72 * H, 1),
    }


def has_text(text: str, lang: str) -> bool:
    """Whether OCR found words to translate (not just digits, a handle or an icon)."""
    import re
    letters = r"[А-Яа-яЁё]" if "rus" in lang else r"[^\W\d_]"
    words = re.findall(letters + "{2,}", text)
    # "еее" is three dots on an icon, not a word
    return any(len(set(w.lower())) > 1 for w in words)


def extract(tracker: BoxCaptionTracker, src: str, W: int, H: int, fps: float, lang: str, use_ocr: bool, log) -> dict | None:
    """Segments → caption phrases with text, cover tracks and the measured box style."""
    from . import ocr
    from .media import read_frames_at

    segs = _merge_pieces(tracker.finish(fps), H)
    if not segs:
        return None
    reps = [s.rep() for s in segs]
    frames = read_frames_at(src, [b.frame for b in reps], W, H)
    texts = [""] * len(segs)
    if use_ocr:
        reason = ocr.available()
        if reason:
            log(f"  OCR skipped: {reason}")
        else:
            log(f"  OCR ({lang}+eng) of {len(segs)} boxes…")
            res = ocr.recognize([ocr_crop(frames[b.frame], b) for b in reps], f"{lang}+eng", psm="6")
            texts = [r["text"] for r in res]
            for s, r in zip(segs, res):
                s.text, s.confidence = r["text"], r["confidence"]
    style = measure_box_style(segs, frames, W, H)
    pad = max(2, round(W * 0.004))
    phrases, skipped = [], 0
    for s, text in zip(segs, texts):
        if use_ocr and not has_text(text, lang):
            skipped += 1   # digits-only cards, @handles, icons: left as they are
            continue
        upper = sum(c.isupper() for c in text) > 0.6 * max(1, sum(c.isalpha() for c in text))
        phrases.append({
            "id": f"c{s.index}",
            "start": round(s.start / fps, 3),
            "end": round(s.end / fps, 3),
            "text": text or f"#{s.index}",
            "confidence": round(s.confidence, 1),
            "card": s.card,
            "uppercase": upper,
            "lines": max(1, round(s.rep().h / max(1.0, style["font_px"] * 1.45))),
            "track": track_of(s, fps, pad),
        })
    log(f"captions (boxes): {len(phrases)} phrases with text, {sum(p['card'] for p in phrases)} title cards; "
        f"{skipped} boxes without words left as they are")
    return {"kind": "box", "style": style, "phrases": phrases}


def _runs(flags: np.ndarray, gap: int) -> list[tuple[int, int]]:
    idx = np.nonzero(flags)[0]
    if not len(idx):
        return []
    runs, a = [], idx[0]
    for p, q in zip(idx, idx[1:]):
        if q - p > gap:
            runs.append((a, p + 1))
            a = q
    runs.append((a, idx[-1] + 1))
    return runs


def _text_block(stack: np.ndarray) -> tuple[int, int, int, int] | None:
    """The densest block of text lines in a static-stroke mask (x, y, w, h)."""
    rows = _runs(stack.mean(axis=1) > 0.02, gap=16)  # line spacing of small print
    if not rows:
        return None
    a, b = max(rows, key=lambda r: stack[r[0]:r[1]].sum())
    cols = _runs(stack[a:b].mean(axis=0) > 0.01, gap=14)
    if not cols:
        return None
    c0, c1 = max(cols, key=lambda c: stack[a:b, c[0]:c[1]].sum())
    if c1 - c0 < 30 or b - a < 5:
        return None
    return c0, a, c1 - c0, b - a


def find_notes(src: str, W: int, H: int, fps: float, lang: str, use_ocr: bool, log,
               band=(0.86, 0.99), min_s: float = 0.6) -> list[dict]:
    """Small static white text near the bottom (a disclaimer on b-roll, say): when it is
    on screen, where, and what it says."""
    from . import ocr
    from .media import read_frames, read_frames_at

    w, h = 540, int(round(540 * H / W))
    y0, y1 = int(band[0] * h), int(band[1] * h)
    k = np.ones((7, 7), np.uint8)
    on, masks = [], {}
    for i, f in enumerate(read_frames(src, w, h)):
        g = cv2.cvtColor(f[y0:y1], cv2.COLOR_BGR2GRAY)
        th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, k) > 60
        if th.mean() > 0.01:
            on.append(i)
            masks[i] = th
    spans: list[list[int]] = []
    for i in on:
        if spans and i - spans[-1][1] <= int(0.3 * fps):
            spans[-1][1] = i
        else:
            spans.append([i, i])
    spans = [s for s in spans if (s[1] - s[0] + 1) >= min_s * fps]
    notes, rects = [], []
    for a, b in spans:
        stack = np.mean([masks[i] for i in range(a, b + 1) if i in masks], axis=0) > 0.8   # static strokes only
        box = _text_block(stack)
        if box is None:
            continue
        bx, by, bw, bh = box
        sx, sy = W / w, H / h
        rects.append((a, b, int(bx * sx), int((by + y0) * sy), int(bw * sx), int(bh * sy)))
    if not rects:
        return []
    texts = [""] * len(rects)
    if use_ocr and not ocr.available():
        frames = read_frames_at(src, [(a + b) // 2 for a, b, *_ in rects], W, H)
        imgs = []
        for a, b, x, y, ww, hh in rects:
            crop = frames[(a + b) // 2][max(0, y - 6): y + hh + 6, max(0, x - 6): x + ww + 6]
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            text = (hsv[..., 2] > 190) & (hsv[..., 1] < 70)
            imgs.append(cv2.copyMakeBorder(np.where(text, 0, 255).astype(np.uint8), 20, 20, 20, 20,
                                           cv2.BORDER_CONSTANT, value=255))
        texts = [r["text"] for r in ocr.recognize(imgs, lang, psm="6")]
    for (a, b, x, y, ww, hh), text in zip(rects, texts):
        if use_ocr and len(re.findall(r"[^\W\d_]", text)) < 10:
            continue   # a few letters out of texture: not a notice
        notes.append({"id": f"n{len(notes)}", "start": round(a / fps, 3), "end": round((b + 1) / fps, 3),
                      "text": text, "bbox": [x, y, ww, hh], "lines": max(1, round(hh / (0.018 * H)))})
    log(f"notes: {len(notes)} small on-screen texts near the bottom")
    return notes
