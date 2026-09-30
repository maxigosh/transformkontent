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


def localize_main(argv: list[str]) -> int:
    from .localize import LocalizeOptions, run as localize

    p = argparse.ArgumentParser(
        prog="vidextract localize",
        description="Swap the overlay for your logo, drop the original speech, translate it and dub it "
                    "with ElevenLabs; captions are rebuilt from the new voice's timing.")
    p.add_argument("project", help="project directory produced by `vidextract <video> -o <dir>`")
    p.add_argument("--logo", help="logo image (PNG/SVG/WebP with transparency) to replace the overlay")
    p.add_argument("--lang", default="English", help="target language name (default English)")
    p.add_argument("--lang-code", default="en", help="short code used in file names (default en)")
    p.add_argument("--translation", help="use this translation JSON ([{id, text}]) instead of calling Claude")
    p.add_argument("--translate-only", action="store_true", help="write translation.<code>.json and stop")
    p.add_argument("--tts", choices=["elevenlabs", "mock"], default="elevenlabs",
                   help="voice engine; mock = offline test tone (default elevenlabs, needs ELEVENLABS_API_KEY)")
    p.add_argument("--voice", help="ElevenLabs voice_id")
    p.add_argument("--tts-model", help="ElevenLabs model_id (default eleven_multilingual_v2)")
    a = p.parse_args(argv)
    localize(a.project, LocalizeOptions(
        logo=a.logo, lang=a.lang, lang_code=a.lang_code, translation=a.translation,
        translate_only=a.translate_only, tts=a.tts, voice=a.voice, tts_model=a.tts_model))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "localize":
        return localize_main(argv[1:])
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
