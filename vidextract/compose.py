"""Write the extracted layers as a HyperFrames project (index.html + assets)."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent
HYPERFRAMES_VERSION = "0.8.96"
FONT_FAMILY = "Rubik"
FONT_CAP_RATIO = 0.70   # Rubik: capHeight 700 / unitsPerEm 1000


def _vendor_gsap(dst: Path) -> str:
    """Copy GSAP next to the project so renders don't depend on a CDN."""
    for cand in (REPO / "node_modules" / "gsap" / "dist" / "gsap.min.js",):
        if cand.exists():
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy(cand, dst / "gsap.min.js")
            return "assets/vendor/gsap.min.js"
    return "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"


def _caption_css(style: dict, W: int, H: int) -> tuple[str, int, int]:
    cap_h = style["cap_height_frac"] * H
    font_px = max(8, round(cap_h / FONT_CAP_RATIO))
    box_h = round(font_px * 1.8)
    top = round(style["center"]["y"] * H - box_h / 2)
    # x centre: shift the full-width box so the word centre lands where it was measured
    dx = round((style["center"]["x"] - 0.5) * W)
    stroke = round(style["stroke_px"] * 2, 1)
    css = f"""
      .caption-word {{
        position: absolute;
        left: {dx}px;
        width: {W}px;
        top: {top}px;
        height: {box_h}px;
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10;
        pointer-events: none;
      }}
      .caption-text {{
        display: block;
        font-family: "{FONT_FAMILY}", sans-serif;
        font-weight: 900;
        font-size: {font_px}px;
        line-height: 1;
        color: {style["text_color"]};
        -webkit-text-stroke: {stroke}px {style["stroke_color"]};
        paint-order: stroke fill;
        {"text-transform: uppercase;" if style.get("uppercase") else ""}
        white-space: nowrap;
        max-width: {round(W * 0.84)}px;
      }}"""
    return css, font_px, box_h


def build_html(m: dict, gsap_src: str) -> str:
    W, H = m["canvas"]["width"], m["canvas"]["height"]
    D = m["source"]["duration"]
    fps = m["source"]["fps"]
    layers = m["layers"]
    ov, caps, audio = layers["overlay"], layers["captions"], layers["audio"]

    css_caption = ""
    if caps:
        css_caption, _, _ = _caption_css(caps["style"], W, H)

    clips = [f"""      <!-- layer 0: footage with overlay/captions painted out -->
      <video id="plate" class="clip" src="{layers["plate"]["src"]}" muted playsinline
        data-start="0" data-duration="{D}" data-track-index="0"></video>"""]
    if ov:
        z = ov["zone_px"]
        clips.append(f"""      <!-- layer 1: animated sponsor overlay, VP9 with alpha ({len(ov["states"])} states, see elements.json) -->
      <video id="overlay" class="clip" src="{ov["src"]}" muted playsinline
        data-start="0" data-duration="{D}" data-track-index="1"
        style="top: {z[1]}px; height: {z[3] - z[1]}px"></video>""")
    if caps:
        clips.append("      <!-- layer 2: word captions (editable text) -->")
        for w in caps["words"]:
            dur = round(max(1 / fps, w["end"] - w["start"]), 3)
            pop = f' data-pop="{",".join(str(x) for x in w["pop"])}"' if w.get("pop") else ""
            clips.append(
                f'      <div id="{w["id"]}" class="clip caption-word" data-start="{w["start"]}" data-duration="{dur}" '
                f'data-track-index="2"{pop}><span class="caption-text">{html.escape(w["text"])}</span></div>')
    if audio:
        clips.append(f"""      <!-- layer 3: original soundtrack -->
      <audio id="audio" src="{audio["src"]}" data-start="0" data-duration="{D}" data-track-index="3" data-volume="1"></audio>""")

    pop = (caps or {}).get("style", {}).get("pop_in", {}).get("scales") or [1]
    exit_ = (caps or {}).get("style", {}).get("exit", {}).get("scales") or []
    script = f"""
      // Pop-in measured from the source: glyph scale per frame at {fps:g} fps.
      // Each word carries its own measured curve in data-pop; POP is the typical one.
      const POP = {json.dumps(pop)};
      const EXIT = {json.dumps(exit_)};
      const FRAME = 1 / {fps:g};
      const tl = gsap.timeline({{ paused: true }});
      document.querySelectorAll("#root .caption-word").forEach((clip) => {{
        const word = clip.querySelector(".caption-text");
        const start = parseFloat(clip.dataset.start);
        const end = start + parseFloat(clip.dataset.duration);
        const pop = clip.dataset.pop ? clip.dataset.pop.split(",").map(Number) : POP;
        if (!pop.length) return;
        tl.set(word, {{ scale: pop[0] }}, start);
        pop.slice(1).forEach((s, i) => tl.to(word, {{ scale: s, duration: FRAME, ease: "none" }}, start + i * FRAME));
        if (pop[pop.length - 1] !== 1) tl.to(word, {{ scale: 1, duration: FRAME, ease: "none" }}, start + (pop.length - 1) * FRAME);
        EXIT.forEach((s, i) => tl.to(word, {{ scale: s, duration: FRAME, ease: "none" }}, end - (EXIT.length - i) * FRAME));
      }});
      window.__timelines["main"] = tl;"""

    body = "\n".join(clips)
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title>vidextract — {html.escape(Path(m["source"]["path"]).stem)}</title>
    <script src="{gsap_src}"></script>
    <style>
      @font-face {{
        font-family: "{FONT_FAMILY}";
        src: url("assets/fonts/{FONT_FAMILY}.ttf") format("truetype");
        font-weight: 300 900;
        font-display: block;
      }}
      body {{
        margin: 0;
        background: #000;
      }}
      #root {{
        position: relative;
        width: 100%;
        height: 100%;
        overflow: hidden;
        background: #000;
      }}
      #plate {{
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        object-fit: cover;
      }}
      #overlay {{
        position: absolute;
        left: 0;
        width: 100%;
        z-index: 5;
      }}{css_caption}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{D}"
      data-width="{W}" data-height="{H}">
{body}
    </div>
    <script>{script}
    </script>
  </body>
</html>
"""


def write_project(out: Path, manifest: dict) -> None:
    name = out.resolve().name
    fonts = out / "assets" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "assets" / "fonts" / f"{FONT_FAMILY}.ttf", fonts / f"{FONT_FAMILY}.ttf")
    shutil.copy(HERE / "assets" / "fonts" / "OFL.txt", fonts / "OFL.txt")
    gsap_src = _vendor_gsap(out / "assets" / "vendor")
    (out / "index.html").write_text(build_html(manifest, gsap_src))
    (out / "meta.json").write_text(json.dumps({"id": name, "name": name}, indent=2))
    (out / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        "media": {"autoProxy": True},
    }, indent=2))
    pin = f"hyperframes@{HYPERFRAMES_VERSION}"
    (out / "package.json").write_text(json.dumps({
        "name": name, "private": True, "type": "module",
        "scripts": {
            "dev": f"npx --yes {pin} preview",
            "check": f"npx --yes {pin} check",
            "render": f"npx --yes {pin} render",
        },
    }, indent=2))
