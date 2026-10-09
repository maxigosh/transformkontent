"""Box captions: segments split on new text, sliding boxes keep a track, title cards."""

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vidextract.boxcaps import BoxCaptionTracker, _merge_pieces, track_of  # noqa: E402

W, H, FPS = 360, 640, 25.0
RED = (11, 10, 224)


def frame(i: int) -> np.ndarray:
    f = np.full((H, W, 3), (90, 70, 60), np.uint8)
    cv2.circle(f, (180, 200), 80, (150, 140, 200), -1)          # a "face" that is not a caption

    def box(x, y, w, h, text):
        cv2.rectangle(f, (x, y), (x + w, y + h), RED, -1)
        cv2.putText(f, text, (x + 18, y + h - 18), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3)

    if 5 <= i < 30:
        box(80, 440, 200, 60, "AB AB")
    elif 32 <= i < 60:
        box(80, 440, 200, 60, "CD CD")          # same box, new text: a new caption
    elif 62 <= i < 80:
        box(80, 440 - (i - 62) * 6, 200, 60, "EF EF")   # slides up
    elif 85 <= i < 100:
        box(40, 100, 280, 420, "XY")            # a title card
    elif 105 <= i < 120:
        box(40, 440, 120, 60, "GH")             # a box torn in two pieces
        box(200, 440, 120, 60, "IJ")
    return f


def test_box_segments():
    tr = BoxCaptionTracker(W, H)
    for i in range(125):
        tr.push(i, frame(i))
    segs = _merge_pieces(tr.finish(FPS), H)
    spans = [(s.start, s.end, s.card) for s in segs]
    assert spans == [(5, 30, False), (32, 60, False), (62, 80, False), (85, 100, True), (105, 120, False)], spans
    assert len(track_of(segs[0], FPS, 2)) == 1                       # still: one keyframe
    moving = track_of(segs[2], FPS, 2)
    assert len(moving) > 5 and moving[0][2] > moving[-1][2]          # follows the slide up
    t, cx, cy, w, h = track_of(segs[4], FPS, 2)[0]
    assert w >= 280 and abs(cx - 180) < 3                            # torn pieces covered as one


if __name__ == "__main__":
    test_box_segments()
    print("ok")
