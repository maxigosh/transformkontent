"""Clean plate: the footage with burned-in overlay and captions painted out."""

from __future__ import annotations

import cv2
import numpy as np


class PlateCleaner:
    """Inpaints masked regions at reduced resolution (fast, and the areas under
    overlays are usually a soft blurred fill anyway), then feathers the patch in."""

    def __init__(self, frame_h: int, scale: float = 0.25, caption_scale: float = 0.5):
        self.scale = scale
        self.caption_scale = caption_scale
        g = max(3, int(frame_h * 0.004)) | 1
        self.k_grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (g, g))
        self.feather = max(1.0, frame_h * 0.0015)

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

    def clean(self, frame: np.ndarray, overlay: tuple[int, np.ndarray] | None,
              caption: tuple[int, np.ndarray] | None) -> np.ndarray:
        out = frame.copy()
        if overlay is not None:
            y0, alpha = overlay
            m = (alpha > 20).astype(np.uint8)
            if m.any():
                ys = np.nonzero(m.any(1))[0]
                a = max(0, ys[0] - 16)
                b = min(m.shape[0], ys[-1] + 17)
                out[y0 + a:y0 + b] = self._patch(out[y0 + a:y0 + b], m[a:b], self.scale)
        if caption is not None:
            y0, mask = caption
            if mask.any():
                out[y0:y0 + mask.shape[0]] = self._patch(out[y0:y0 + mask.shape[0]], mask, self.caption_scale)
        return out
