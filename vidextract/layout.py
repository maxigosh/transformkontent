"""Pass A: low-resolution scan of the whole video.

Finds where the burned-in layers live (graphic overlay zone, caption band),
the dominant hue of the overlay graphics, and the shot cuts of the footage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .media import VideoInfo, even, read_frames

SCAN_WIDTH = 180


def color_gradient(bgr: np.ndarray, k: int) -> np.ndarray:
    """Morphological gradient, max over B/G/R — catches iso-luminant colour edges."""
    return cv2.morphologyEx(bgr, cv2.MORPH_GRADIENT, np.ones((k, k), np.uint8)).max(axis=2)


def graphics_mask(bgr: np.ndarray, hsv: np.ndarray | None = None) -> np.ndarray:
    """Saturated regions bounded by crisp edges. Burned-in graphics have hard
    outlines; the footage behind them (blurred fill, vignette) does not."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv
    edge = color_gradient(bgr, 2) > 40
    sat = (hsv[..., 1] > 110) & (hsv[..., 2] > 80)
    n, lab, stats, _ = cv2.connectedComponentsWithStats((sat & ~edge).astype(np.uint8), connectivity=4)
    if n <= 1:
        return np.zeros(sat.shape, bool)
    lab = lab.astype(np.int32)
    k = np.ones((3, 3), np.uint8)
    # boundary pixels of each region, and how many of them sit on a crisp edge
    grown = cv2.dilate(lab.astype(np.float32), k).astype(np.int32)
    shrunk = cv2.erode(lab.astype(np.float32), k).astype(np.int32)
    boundary = (lab > 0) & (shrunk != lab)
    per_boundary = np.bincount(lab[boundary], minlength=n)
    edge_touch = cv2.dilate(edge.astype(np.uint8), k) > 0
    per_crisp = np.bincount(lab[boundary & edge_touch], minlength=n)
    ratio = per_crisp / np.maximum(per_boundary, 1)
    keep = (ratio > 0.6) & (stats[:, cv2.CC_STAT_AREA] >= 6)
    keep[0] = False
    region = keep[lab]
    # add the outlines themselves back
    return (region | (cv2.dilate(region.astype(np.uint8), k) > 0) & edge) & (grown >= 0)


def outlined_text_mask(hsv: np.ndarray, radius: int) -> np.ndarray:
    """White glyph cores that sit right next to a dark outline (meme/TikTok captions)."""
    core = ((hsv[..., 2] >= 200) & (hsv[..., 1] <= 60)).astype(np.uint8)
    dark = (hsv[..., 2] <= 70).astype(np.uint8)
    k = np.ones((2 * radius + 1, 2 * radius + 1), np.uint8)
    return (core > 0) & (cv2.dilate(dark, k) > 0)


def _run_around_peak(profile: np.ndarray, rel: float) -> tuple[int, int] | None:
    if profile.max() <= 0:
        return None
    peak = int(profile.argmax())
    thr = profile.max() * rel
    a = peak
    while a > 0 and profile[a - 1] >= thr:
        a -= 1
    b = peak
    while b < len(profile) - 1 and profile[b + 1] >= thr:
        b += 1
    return a, b + 1


@dataclass
class Layout:
    scan_w: int
    scan_h: int
    overlay_zone: tuple[float, float] | None  # (top, bottom) as fractions of frame height
    overlay_hue: int | None                   # OpenCV hue 0..179 of the overlay's body colour
    overlay_strength: float
    caption_band: tuple[float, float] | None
    caption_strength: float
    cuts: list[int] = field(default_factory=list)  # frame indices where a new shot starts
    n_frames: int = 0


def scan(info: VideoInfo, progress=None) -> Layout:
    sw = SCAN_WIDTH
    sh = even(sw * info.height / info.width)
    g_acc = np.zeros((sh, sw), np.float64)
    t_acc = np.zeros((sh, sw), np.float64)
    hue_hist = np.zeros(180, np.float64)
    hists = []
    n = 0
    for frame in read_frames(info.path, sw, sh):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        g = graphics_mask(frame, hsv)
        g_acc += g
        hue_hist += np.bincount(hsv[..., 0][g], minlength=180)[:180]
        t_acc += outlined_text_mask(hsv, radius=2)
        hists.append(strip_hists(hsv))
        n += 1
        if progress and n % 100 == 0:
            progress(n)
    if n == 0:
        raise RuntimeError("no frames decoded")

    g_freq = g_acc / n
    t_freq = t_acc / n

    overlay_zone, overlay_strength, overlay_hue = None, float(g_freq.mean(1).max()), None
    run = _run_around_peak(cv2.GaussianBlur(g_freq.mean(1)[:, None], (1, 9), 0).ravel(), 0.2)
    if run and overlay_strength > 0.01:
        a, b = run
        pad = max(2, int(0.03 * sh))
        overlay_zone = (max(0, a - pad) / sh, min(sh, b + pad) / sh)
        smooth = np.convolve(np.r_[hue_hist[-8:], hue_hist, hue_hist[:8]], np.ones(9) / 9, "same")[8:-8]
        overlay_hue = int(smooth.argmax())

    caption_band, caption_strength = None, float(t_freq.mean(1).max())
    run = _run_around_peak(t_freq.mean(1), 0.15)
    if run and caption_strength > 0.001:
        a, b = run
        pad = max(3, int(0.9 * (b - a)))  # room for pop-in overshoot
        caption_band = (max(0, a - pad) / sh, min(sh, b + pad) / sh)

    excluded = [z for z in (overlay_zone, caption_band) if z]
    cuts = detect_cuts(footage_hists(np.stack(hists), excluded), info.fps)
    return Layout(sw, sh, overlay_zone, overlay_hue, overlay_strength, caption_band, caption_strength, cuts, n)


STRIPS = 16


def strip_hists(hsv: np.ndarray) -> np.ndarray:
    """Per-horizontal-strip HSV histograms, so overlay rows can be excluded later."""
    out = []
    for rows in np.array_split(hsv, STRIPS, axis=0):
        h = cv2.calcHist([rows], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
        out.append(h.ravel())
    return np.stack(out).astype(np.float32)


def footage_hists(strips: np.ndarray, excluded: list[tuple[float, float]]) -> list[np.ndarray]:
    keep = []
    for s in range(STRIPS):
        a, b = s / STRIPS, (s + 1) / STRIPS
        if not any(a < z1 and b > z0 for z0, z1 in excluded):
            keep.append(s)
    keep = keep or list(range(STRIPS))
    total = strips[:, keep].sum(1)
    return [cv2.normalize(h, None).astype(np.float32) for h in total]


def detect_cuts(hists: list[np.ndarray], fps: float) -> list[int]:
    d = np.zeros(len(hists))
    for i in range(1, len(hists)):
        d[i] = cv2.compareHist(hists[i - 1], hists[i], cv2.HISTCMP_BHATTACHARYYA)
    cuts: list[int] = []
    win = max(3, int(fps * 0.5))
    min_gap = int(fps * 0.4)
    for i in range(1, len(d)):
        local = np.r_[d[max(1, i - win):i], d[i + 1:i + 1 + win]]
        base = np.median(local) if len(local) else 0.0
        if d[i] > 0.28 and d[i] > 3.0 * base + 0.08 and d[i] == d[max(1, i - 2):i + 3].max():
            if not cuts or i - cuts[-1] >= min_gap:
                cuts.append(i)
    return cuts
