"""Graphic overlay layer (sponsor banners, stickers, logos) burned into the footage.

Per frame we pull an alpha matte for the overlay zone:
  1. body   - pixels of the overlay's dominant hue, holes filled (text, icons inside);
  2. extras - crisp saturated/white pieces touching the body (coins, stars, hair);
  3. edges  - slightly feathered.
The footage behind overlays in short-form edits is a blurred fill, so
"crisp + saturated + attached to the body" is a strong cue.

Afterwards the per-frame signatures are segmented into stable states
(each distinct banner) and transitions between them.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    flood = mask.copy()
    ff = np.zeros((h + 2, w + 2), np.uint8)
    # seed from every border pixel that is background
    border = np.zeros_like(mask, bool)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    ys, xs = np.nonzero(border & (mask == 0))
    for y, x in zip(ys[:: max(1, len(ys) // 64)], xs[:: max(1, len(xs) // 64)]):
        if flood[y, x] == 0:
            cv2.floodFill(flood, ff, (int(x), int(y)), 2)
    return np.where(flood == 2, 0, 1).astype(np.uint8)


class OverlayMatter:
    def __init__(self, hue: int, frame_height: int, hue_tol: int = 16):
        self.hue = hue
        self.hue_tol = hue_tol
        s = max(1, frame_height // 640)
        self.k_small = np.ones((2 * s + 1, 2 * s + 1), np.uint8)
        self.k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (6 * s + 1, 6 * s + 1))
        self.k_touch = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (10 * s + 1, 10 * s + 1))
        self.min_area_frac = 0.002

    def matte(self, bgr: np.ndarray) -> np.ndarray:
        """Return uint8 alpha (0..255) for the overlay zone crop."""
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[..., 0].astype(np.int16), hsv[..., 1], hsv[..., 2]
        dh = np.abs(h - self.hue)
        dh = np.minimum(dh, 180 - dh)
        grad = cv2.morphologyEx(bgr, cv2.MORPH_GRADIENT, self.k_small).max(axis=2)
        crisp = cv2.dilate((grad > 45).astype(np.uint8), self.k_close)

        strong = (dh <= self.hue_tol) & (s > 110) & (v > 70)
        # lighter tints of the same hue (gradients, highlights) count only where crisp
        soft = (dh <= self.hue_tol + 8) & (s > 45) & (v > 120) & (crisp > 0)
        body = (strong | soft).astype(np.uint8)
        body = cv2.morphologyEx(body, cv2.MORPH_OPEN, self.k_small)
        body = cv2.morphologyEx(body, cv2.MORPH_CLOSE, self.k_close)
        body = self._drop_small(body)
        if body.any():
            # pale tints of the body hue (glossy highlights) attached to the body
            pale = ((dh <= self.hue_tol + 12) & (s > 30) & (v > 140)).astype(np.uint8)
            body = body | self._touching(pale & (1 - body), body)
            body = _fill_holes(cv2.morphologyEx(body, cv2.MORPH_CLOSE, self.k_small))

        colorful = ((s > 90) & (v > 110)) | ((v > 215) & (s < 40))
        extras = (colorful & (crisp > 0) & (body == 0)).astype(np.uint8)
        if extras.any() and body.any():
            touch = cv2.dilate(body, self.k_touch)
            n, lab, stats, _ = cv2.connectedComponentsWithStats(extras, connectivity=8)
            hit = np.bincount(lab[touch > 0], minlength=n)
            keep = np.zeros(n, np.uint8)
            max_area = body.sum() * 0.6
            for i in range(1, n):
                if hit[i] > 0 and stats[i, cv2.CC_STAT_AREA] < max_area:
                    keep[i] = 1
            extras = keep[lab]
            body = _fill_holes(cv2.morphologyEx(body | extras, cv2.MORPH_CLOSE, self.k_small))
        alpha = cv2.GaussianBlur(body.astype(np.float32), (0, 0), 0.9)
        return np.clip(alpha * 255.0, 0, 255).astype(np.uint8)

    def _touching(self, cand: np.ndarray, body: np.ndarray) -> np.ndarray:
        """Components of `cand` that touch `body` (and are not huge)."""
        if not cand.any():
            return cand
        touch = cv2.dilate(body, self.k_small)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
        hit = np.bincount(lab[touch > 0], minlength=n)
        keep = ((hit > 0) & (stats[:, cv2.CC_STAT_AREA] < body.sum() * 0.6)).astype(np.uint8)
        keep[0] = 0
        return keep[lab]

    def _drop_small(self, m: np.ndarray) -> np.ndarray:
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        min_area = self.min_area_frac * m.size
        keep = (stats[:, cv2.CC_STAT_AREA] >= min_area).astype(np.uint8)
        keep[0] = 0
        return keep[lab]


def signature(bgr: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Tiny premultiplied thumbnail used to tell overlay states apart."""
    a = alpha.astype(np.float32)[..., None] / 255.0
    pre = bgr.astype(np.float32) * a
    thumb = cv2.resize(np.dstack([pre, a[..., 0] * 255.0]), (24, 10), interpolation=cv2.INTER_AREA)
    return thumb.astype(np.float16).ravel()


@dataclass
class OverlayState:
    id: str
    frames: list[tuple[int, int]]  # stable runs [start, end) that show this state
    rep_frame: int
    coverage: float                # mean alpha of the zone


def segment_states(sigs: np.ndarray, fps: float, stable_thr: float = 4.5, cluster_thr: float = 12.0,
                   min_run_s: float = 0.35, lag_s: float = 0.2) -> tuple[list[OverlayState], list[dict]]:
    """Split the overlay track into stable states and the transitions between them.

    Transitions are smooth, so frame-to-frame differences stay small; comparing
    each frame with its neighbours `lag_s` away in both directions exposes them.
    """
    sigs = sigs.astype(np.float32)
    n = len(sigs)
    lag = max(1, int(round(lag_s * fps)))
    d = np.zeros(n, np.float32)
    if n > lag:
        d[lag:] = np.abs(sigs[lag:] - sigs[:-lag]).mean(1)
    back = d
    fwd = np.r_[d[lag:], np.zeros(min(lag, n), np.float32)]
    stable = (back < stable_thr) & (fwd < stable_thr)
    runs: list[tuple[int, int]] = []
    i = 0
    min_len = max(2, int(min_run_s * fps))
    while i < n:
        if stable[i]:
            j = i
            while j + 1 < n and stable[j + 1]:
                j += 1
            if j + 1 - i >= min_len:
                runs.append((i, j + 1))
            i = j + 1
        else:
            i += 1

    # a banner with internal motion (ticker, wink) may split into several runs:
    # merge neighbours that look alike, then cluster runs into states
    reps = [np.median(sigs[a:b], axis=0) for a, b in runs]
    labels: list[int] = []
    centers: list[np.ndarray] = []
    for r in reps:
        d = [np.abs(r - c).mean() for c in centers]
        if d and min(d) < cluster_thr:
            labels.append(int(np.argmin(d)))
        else:
            centers.append(r)
            labels.append(len(centers) - 1)

    alpha_idx = np.arange(3, sigs.shape[1], 4)
    states: list[OverlayState] = []
    for k in range(len(centers)):
        spans = [runs[i] for i in range(len(runs)) if labels[i] == k]
        longest = max(spans, key=lambda s: s[1] - s[0])
        cov = float(centers[k][alpha_idx].mean() / 255.0)
        sid = f"overlay_{k + 1:02d}" + ("_empty" if cov < 0.01 else "")
        states.append(OverlayState(id=sid, frames=spans, rep_frame=(longest[0] + longest[1]) // 2,
                                   coverage=cov))

    timeline: list[dict] = []
    prev_end = 0
    for (a, b), k in zip(runs, labels):
        if a > prev_end:
            timeline.append({"kind": "transition", "start_frame": prev_end, "end_frame": a})
        st = states[k]
        if timeline and timeline[-1].get("state") == st.id:
            timeline[-1]["end_frame"] = b
        else:
            timeline.append({"kind": "state", "state": st.id, "start_frame": a, "end_frame": b})
        prev_end = b
    if prev_end < n:
        timeline.append({"kind": "transition", "start_frame": prev_end, "end_frame": n})
    for seg in timeline:
        seg["start"] = round(seg["start_frame"] / fps, 3)
        seg["end"] = round(seg["end_frame"] / fps, 3)
    return states, timeline
