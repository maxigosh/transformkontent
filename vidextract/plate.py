"""Clean plate: the footage with burned-in overlay and captions painted out."""

from __future__ import annotations

from collections import deque

import cv2
import numpy as np


class PlateCleaner:
    """Overlay areas sit on a soft blurred fill, so a fast low-res inpaint is enough there.

    Caption words sit on the subject (face, shirt), where a spatial inpaint leaves a
    visible smudge. There we first reuse the same pixel from a recent frame of the
    same shot in which it was uncovered (a static camera sees the same background as
    words come and go), and inpaint at full resolution only what was never seen.
    """

    def __init__(self, frame_h: int, fps: float = 30.0, cuts: list[int] = (), scale: float = 0.25,
                 memory_s: float = 0.2):
        self.scale = scale
        g = max(3, int(frame_h * 0.004)) | 1
        self.k_grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (g, g))
        self.feather = max(1.0, frame_h * 0.0015)
        gm = max(5, int(frame_h * 0.014)) | 1   # outline edges and the soft drop shadow
        self.k_mem = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gm, gm))
        self.cuts = set(cuts)
        self.max_age = max(1, int(memory_s * fps))
        self._last = None   # last uncovered pixels of the caption band
        self._age = None    # frames since each pixel was last uncovered
        self._queue: deque = deque()
        self._next = 0      # queue position of the next frame to emit

    def _patch(self, img: np.ndarray, mask: np.ndarray, scale: float) -> np.ndarray:
        h, w = mask.shape
        sw, sh = max(8, int(w * scale)), max(8, int(h * scale))
        small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
        msmall = (cv2.resize(mask, (sw, sh), interpolation=cv2.INTER_AREA) > 0).astype(np.uint8)
        msmall = cv2.dilate(msmall, np.ones((3, 3), np.uint8))
        filled = cv2.inpaint(small, msmall, 3, cv2.INPAINT_TELEA)
        up = cv2.resize(filled, (w, h), interpolation=cv2.INTER_CUBIC)
        soft = cv2.GaussianBlur(cv2.dilate(mask, self.k_grow).astype(np.float32), (0, 0), self.feather)
        soft = np.clip(soft * 1.5, 0, 1)[..., None]
        return (img * (1 - soft) + up * soft).astype(np.uint8)

    def _caption_fill(self, band: np.ndarray, mask: np.ndarray) -> np.ndarray:
        m = cv2.dilate(mask, self.k_mem) > 0
        if self._last is None or self._last.shape != band.shape:
            self._last = band.copy()
            self._age = np.full(m.shape, 10 ** 6, np.int32)
        free = ~m
        self._last[free] = band[free]
        self._age[free] = 0
        self._age[m] += 1
        if not m.any():
            return band
        out = band.copy()
        recent = m & (self._age <= self.max_age)
        out[recent] = self._last[recent]
        never = (m & ~recent).astype(np.uint8)
        if never.any():
            out = cv2.inpaint(out, cv2.dilate(never, np.ones((3, 3), np.uint8)), 5, cv2.INPAINT_TELEA)
        # soften the seam between reused/inpainted pixels and the live frame
        soft = cv2.GaussianBlur(m.astype(np.float32), (0, 0), 1.2)[..., None]
        return (band * (1 - soft) + out * soft).astype(np.uint8)

    # ---- streaming with a small look-ahead ---------------------------------------------
    # A caption word is sometimes missed for a frame (first pop-in frame, motion blur).
    # Unioning each frame's caption mask with its neighbours' keeps those letters out of
    # the background memory and paints them out as well.
    WINDOW = 3

    def push(self, index: int, frame: np.ndarray, overlay, caption, band):
        """Feed frames in order; yields (index, clean_frame) WINDOW frames later."""
        mask = None
        if band is not None:
            mask = caption[1] if caption is not None else np.zeros((band[1] - band[0], frame.shape[1]), np.uint8)
        self._queue.append((index, frame, overlay, mask, band))
        while self._next < len(self._queue) and len(self._queue) - 1 - self._next >= self.WINDOW:
            yield self._emit(self._next)
            self._next += 1
        while self._next > self.WINDOW:   # keep WINDOW emitted frames for look-behind
            self._queue.popleft()
            self._next -= 1

    def flush(self):
        while self._next < len(self._queue):
            yield self._emit(self._next)
            self._next += 1

    def _emit(self, pos: int):
        q = self._queue
        index, frame, overlay, mask, band = q[pos]
        caption = None
        if band is not None:
            union = mask.copy()
            for j in range(max(0, pos - self.WINDOW), min(len(q), pos + self.WINDOW + 1)):
                if q[j][3] is not None and q[j][3].shape == union.shape:
                    union |= q[j][3]
            caption = (band[0], union)
        return index, self.clean(frame, overlay, caption, index, band)

    def clean(self, frame: np.ndarray, overlay: tuple[int, np.ndarray] | None,
              caption: tuple[int, np.ndarray] | None, index: int = -1,
              band: tuple[int, int] | None = None) -> np.ndarray:
        """`band` (y0, y1) keeps the caption memory fed on frames without captions."""
        if index in self.cuts:
            self._last = self._age = None
        out = frame.copy()
        if overlay is not None:
            y0, alpha = overlay
            m = (alpha > 20).astype(np.uint8)
            if m.any():
                ys = np.nonzero(m.any(1))[0]
                a = max(0, ys[0] - 16)
                b = min(m.shape[0], ys[-1] + 17)
                out[y0 + a:y0 + b] = self._patch(out[y0 + a:y0 + b], m[a:b], self.scale)
        if band is not None:
            y0, y1 = band
            mask = np.zeros((y1 - y0, frame.shape[1]), np.uint8)
            if caption is not None:
                mask = caption[1]
            out[y0:y1] = self._caption_fill(out[y0:y1], mask)
        return out
