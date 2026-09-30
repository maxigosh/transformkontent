"""CLI: python -m vidextract <video> -o <project-dir>"""

from __future__ import annotations

import argparse
import sys

from .extract import Options, run


def _zone(s: str) -> tuple[float, float]:
    a, b = (float(x) for x in s.split(":"))
    if not (0 <= a < b <= 1):
        raise argparse.ArgumentTypeError("expected TOP:BOTTOM fractions, e.g. 0.05:0.27")
    return a, b


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="vidextract",
        description="Decompose a short-form video into layers (clean footage, animated overlay with alpha, "
                    "word captions, audio, shots) and assemble them as a HyperFrames project.")
    p.add_argument("video", help="input video (any format ffmpeg can decode)")
    p.add_argument("-o", "--out", required=True, help="output HyperFrames project directory")
    p.add_argument("--width", type=int, default=1080, help="working/canvas width (default 1080)")
    p.add_argument("--ocr-lang", default="rus", help="tesseract language(s), e.g. rus, eng, rus+eng")
    p.add_argument("--no-ocr", action="store_true", help="skip caption text recognition")
    p.add_argument("--overlay-zone", type=_zone, help="force overlay zone TOP:BOTTOM (fractions of height)")
    p.add_argument("--caption-band", type=_zone, help="force caption band TOP:BOTTOM (fractions of height)")
    p.add_argument("--no-overlay", action="store_true", help="don't extract a graphic overlay layer")
    p.add_argument("--no-captions", action="store_true", help="don't extract captions")
    a = p.parse_args(argv)
    run(a.video, a.out, Options(
        width=a.width, ocr_lang=a.ocr_lang, ocr=not a.no_ocr, overlay_zone=a.overlay_zone,
        caption_band=a.caption_band, no_overlay=a.no_overlay, no_captions=a.no_captions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
