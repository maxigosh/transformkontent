"""Prepare a brand logo for animation.

A raster logo gets a transparent background (if it came on a flat one) and is
split into its mark and its wordmark when they are side by side, so the two
can be animated separately: the mark rolls in, the wordmark slides out of it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np


def _remove_flat_background(img: np.ndarray, tol: int = 18) -> np.ndarray:
    """BGRA with the border-connected flat background made transparent."""
    bgr = img[..., :3]
    corners = np.array([bgr[0, 0], bgr[0, -1], bgr[-1, 0], bgr[-1, -1]], np.int16)
    bg = np.median(corners, axis=0)
    near = (np.abs(bgr.astype(np.int16) - bg).max(axis=2) <= tol).astype(np.uint8)
    h, w = near.shape
    flood = near.copy()
    mask = np.zeros((h + 2, w + 2), np.uint8)
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if flood[y, x] == 1:
            cv2.floodFill(flood, mask, (x, y), 2)
    alpha = np.where(flood == 2, 0, 255).astype(np.uint8)
    alpha = cv2.GaussianBlur(alpha, (3, 3), 0)
    return np.dstack([bgr, alpha])


def _crop(img: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(img[..., 3] > 8)
    return img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _split(img: np.ndarray) -> int | None:
    """Column where a roughly square mark ends and the wordmark begins."""
    h, w = img.shape[:2]
    cols = (img[..., 3] > 8).any(axis=0)
    gaps, start = [], None
    for x, filled in enumerate(cols):
        if not filled and start is None:
            start = x
        elif filled and start is not None:
            gaps.append((start, x))
            start = None
    for a, b in gaps:
        if 0.6 <= a / h <= 1.6 and (w - b) > a * 0.8 and b - a >= max(4, w * 0.006):
            return (a + b) // 2
    return None


def prepare(src: str | Path, assets: Path) -> dict:
    """Write the logo (and its parts) into `assets`; returns paths and layout fractions."""
    src = Path(src)
    if src.suffix.lower() == ".svg":
        dst = assets / "logo.svg"
        shutil.copy(src, dst)
        return {"src": f"assets/{dst.name}", "aspect": None, "mark": None, "word": None}

    img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError(f"cannot read logo {src}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 3 or img[..., 3].min() == 255:
        cut_out = _remove_flat_background(img[..., :3])
        # a logo that *is* a flat block would vanish entirely: keep it opaque then
        if (cut_out[..., 3] > 8).mean() > 0.02:
            img = cut_out
        elif img.shape[2] == 3:
            img = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
    img = _crop(img)
    h, w = img.shape[:2]
    cv2.imwrite(str(assets / "logo.png"), img)
    out = {"src": "assets/logo.png", "aspect": round(w / h, 4), "mark": None, "word": None}
    cut = _split(img)
    if cut is not None:
        mark, word = img[:, :cut], img[:, cut:]
        ys, xs = np.nonzero(mark[..., 3] > 8)
        mark_box = (xs.min(), xs.max() + 1)
        ys, xs = np.nonzero(word[..., 3] > 8)
        word_box = (cut + xs.min(), cut + xs.max() + 1)
        cv2.imwrite(str(assets / "logo_mark.png"), img[:, mark_box[0]:mark_box[1]])
        cv2.imwrite(str(assets / "logo_word.png"), img[:, word_box[0]:word_box[1]])
        out["mark"] = {"src": "assets/logo_mark.png", "left": mark_box[0] / w, "width": (mark_box[1] - mark_box[0]) / w}
        out["word"] = {"src": "assets/logo_word.png", "left": word_box[0] / w, "width": (word_box[1] - word_box[0]) / w}
    return out
